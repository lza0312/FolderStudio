# -*- coding: utf-8 -*-
"""
Folder Studio - 文件夹外显名 / 图标 / 属性 + 链接工具

Tab 1  外显名 / 图标 / 悬浮标注 / 属性
    通过 desktop.ini 的 [.ShellClassInfo] 段设置 LocalizedResourceName（外显名）、
    IconResource（图标）与 InfoTip（鼠标悬停提示），并给文件夹加上系统文件夹标志
    （READONLY 或 SYSTEM —— 资源管理器只对这类文件夹解析 desktop.ini）。
    desktop.ini 本身按 Windows 自建的标准保存为 A + HIDDEN + SYSTEM，默认不显示。

Tab 2  链接
    符号链接 / 目录联接(Junction) / 硬链接 / 快捷方式(.lnk)，
    并在界面上给出每种方式的限制与行为说明。

仅依赖 Python 标准库（需要 tkinter）。Windows 专用。
"""

from __future__ import annotations

import ctypes
import datetime
import json
import locale
import os
import re
import subprocess
import sys
import threading
import traceback
from ctypes import wintypes

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    HAS_PIL = True
except Exception:  # 生成角标图标需要 Pillow；缺失时退化为仅用文字记录
    HAS_PIL = False
    Image = ImageDraw = ImageFont = ImageTk = None

# ---------------------------------------------------------------------------
# Win32 基础封装
# ---------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
kernel32.GetFileAttributesW.restype = wintypes.DWORD
kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.SetFileAttributesW.restype = wintypes.BOOL
kernel32.RemoveDirectoryW.argtypes = [wintypes.LPCWSTR]
kernel32.RemoveDirectoryW.restype = wintypes.BOOL
kernel32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
kernel32.DeleteFileW.restype = wintypes.BOOL

kernel32.CreateSymbolicLinkW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD
]
kernel32.CreateSymbolicLinkW.restype = ctypes.c_ubyte
kernel32.CreateHardLinkW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID
]
kernel32.CreateHardLinkW.restype = wintypes.BOOL

shell32.IsUserAnAdmin.argtypes = []
shell32.IsUserAnAdmin.restype = wintypes.BOOL
shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int
]
shell32.ShellExecuteW.restype = wintypes.HINSTANCE
shell32.SHChangeNotify.argtypes = [
    ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
]
shell32.SHChangeNotify.restype = None

INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

# 文件属性位
A_READONLY = 0x00000001
A_HIDDEN = 0x00000002
A_SYSTEM = 0x00000004
A_DIRECTORY = 0x00000010
A_ARCHIVE = 0x00000020
A_DEVICE = 0x00000040
A_NORMAL = 0x00000080
A_TEMPORARY = 0x00000100
A_SPARSE = 0x00000200
A_REPARSE_POINT = 0x00000400
A_COMPRESSED = 0x00000800
A_OFFLINE = 0x00001000
A_NOT_INDEXED = 0x00002000
A_ENCRYPTED = 0x00004000
A_INTEGRITY = 0x00008000
A_VIRTUAL = 0x00010000
A_NO_SCRUB = 0x00020000
A_RECALL_OPEN = 0x00040000
A_PINNED = 0x00080000
A_UNPINNED = 0x00100000
A_RECALL_DATA = 0x00400000

# 只有这些位可以通过 SetFileAttributes 安全回写
ATTR_SETTABLE = (
    A_READONLY | A_HIDDEN | A_SYSTEM | A_ARCHIVE | A_TEMPORARY |
    A_OFFLINE | A_NOT_INDEXED | A_NO_SCRUB | A_PINNED | A_UNPINNED |
    A_RECALL_OPEN | A_RECALL_DATA
)

# (显示名, 徽章背景, 徽章文字色, 说明)
ATTR_INFO = [
    (A_READONLY, "READONLY 只读", "#4a3410", "#ffcf70", "资源管理器只对 SYSTEM / READONLY 文件夹解析 desktop.ini"),
    (A_SYSTEM, "SYSTEM 系统", "#3a2a58", "#d3b3ff", "与 READONLY 一样，是 desktop.ini 生效的系统文件夹标志"),
    (A_HIDDEN, "HIDDEN 隐藏", "#2b3138", "#a9b6c4", "默认不在资源管理器中显示"),
    (A_DIRECTORY, "DIRECTORY 目录", "#123046", "#7fc9ff", "这是一个文件夹"),
    (A_ARCHIVE, "ARCHIVE 存档", "#2b3138", "#a9b6c4", "普通文件 / 文件夹的常见标志"),
    (A_REPARSE_POINT, "REPARSE POINT 重解析点", "#0d3d40", "#67e8d0", "符号链接 / 目录联接 / 云占位文件等"),
    (A_COMPRESSED, "COMPRESSED 压缩", "#163a24", "#8ce0a4", "NTFS 压缩"),
    (A_ENCRYPTED, "ENCRYPTED 加密", "#163a24", "#8ce0a4", "EFS 加密"),
    (A_NOT_INDEXED, "NOT INDEXED 不索引", "#2b3138", "#a9b6c4", "不纳入内容索引"),
    (A_TEMPORARY, "TEMPORARY 临时", "#2b3138", "#a9b6c4", "尽量留在内存中"),
    (A_SPARSE, "SPARSE 稀疏", "#2b3138", "#a9b6c4", "稀疏文件"),
    (A_OFFLINE, "OFFLINE 脱机", "#4a3410", "#ffcf70", "内容位于远程存储"),
    (A_NORMAL, "NORMAL 普通", "#2b3138", "#a9b6c4", "未设置其它属性"),
]

SHCNE_UPDATEDIR = 0x00001000
SHCNE_UPDATEITEM = 0x00002000
SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000
SHCNF_PATHW = 0x0005
SYMBOLIC_LINK_FLAG_DIRECTORY = 0x1
SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED = 0x2
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C
ERROR_INVALID_PARAMETER = 87
ERROR_PRIVILEGE_NOT_HELD = 1314

INI_NAME = "desktop.ini"
SECTION = ".ShellClassInfo"
# Windows 自己创建的 desktop.ini 就是 A + HIDDEN + SYSTEM (0x26)，不会显示在文件夹里
INI_STANDARD_ATTRS = A_ARCHIVE | A_HIDDEN | A_SYSTEM
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def app_dir() -> str:
    """打包成单 exe 后，日志要写到 exe 所在目录，而不是临时解压目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def win_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_attrs(path: str) -> int:
    value = kernel32.GetFileAttributesW(os.path.abspath(path))
    if value == INVALID_FILE_ATTRIBUTES:
        raise win_error()
    return int(value)


def set_attr(path: str, mask: int, on: bool) -> int:
    """只回写可由 SetFileAttributes 设置的位，避免把 DIRECTORY 等标志传回去报错。"""
    cur = get_attrs(path) & ATTR_SETTABLE
    new = (cur | mask) if on else (cur & ~mask)
    new &= 0xFFFFFFFF
    if not kernel32.SetFileAttributesW(os.path.abspath(path), new):
        raise win_error()
    return new


def notify_shell() -> None:
    """通知资源管理器重新读取文件夹设置 / 图标。"""
    try:
        shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


def notify_folder(folder: str) -> None:
    """针对具体文件夹发变更通知，让外显名 / 图标 / InfoTip 更快刷新。"""
    folder = os.path.abspath(folder)
    targets = (
        (SHCNE_UPDATEITEM, folder),
        (SHCNE_UPDATEDIR, os.path.dirname(folder)),
    )
    for event, path in targets:
        try:
            buf = ctypes.create_unicode_buffer(path)
            shell32.SHChangeNotify(event, SHCNF_PATHW, buf, None)
        except Exception:
            pass
    notify_shell()


# ---------------------------------------------------------------------------
# desktop.ini 读写
# ---------------------------------------------------------------------------

def _decode_ini(raw: bytes):
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace"), "UTF-16LE"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace"), "UTF-16BE"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8-sig", errors="replace"), "UTF-8"
    try:
        return raw.decode("mbcs"), "ANSI (系统代码页)"
    except (UnicodeError, LookupError):
        return raw.decode(locale.getpreferredencoding(False), errors="replace"), "ANSI"


def read_ini(folder: str):
    """返回 (行列表, 编码描述)。文件不存在时返回 ([], None)。"""
    path = os.path.join(folder, INI_NAME)
    if not os.path.isfile(path):
        return [], None
    with open(path, "rb") as handle:
        raw = handle.read()
    text, enc = _decode_ini(raw)
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), enc


def ini_get(lines, section: str, key: str):
    in_section = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped[1:-1].strip().lower() == section.lower()
            continue
        if in_section and "=" in stripped:
            name, value = stripped.split("=", 1)
            if name.strip().lower() == key.lower():
                return value.strip()
    return None


def ini_set(lines, section: str, key: str, value):
    """设置键值；value 为 None 时删除键。保留其它段、注释与原有内容。"""
    out, in_section, found_section, inserted = [], False, False, False
    for line in lines:
        stripped = line.strip()
        is_header = stripped.startswith("[") and stripped.endswith("]")
        if is_header:
            if in_section and not inserted and value is not None:
                out.append(f"{key}={value}")
                inserted = True
            in_section = stripped[1:-1].strip().lower() == section.lower()
            if in_section:
                found_section = True
            out.append(line)
            continue
        if in_section and "=" in stripped and not stripped.startswith((";", "#")):
            if stripped.split("=", 1)[0].strip().lower() == key.lower():
                if value is not None and not inserted:
                    out.append(f"{key}={value}")
                    inserted = True
                continue  # 跳过原有的这一行
        out.append(line)
    if in_section and not inserted and value is not None:
        while out and not out[-1].strip():
            out.pop()
        out.append(f"{key}={value}")
        inserted = True
    if not found_section and value is not None:
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section}]")
        out.append(f"{key}={value}")
    return out


def _drop_empty_section(lines, section: str):
    """若目标段内已无任何内容，连段标题一起移除。"""
    result, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_target = (
            stripped.startswith("[") and stripped.endswith("]")
            and stripped[1:-1].strip().lower() == section.lower()
        )
        if is_target:
            j, block = i + 1, []
            while j < len(lines):
                nxt = lines[j].strip()
                if nxt.startswith("[") and nxt.endswith("]"):
                    break
                block.append(lines[j])
                j += 1
            if any(item.strip() for item in block):
                result.append(line)
                result.extend(block)
            i = j
            continue
        result.append(line)
        i += 1
    return result


def _has_content(lines) -> bool:
    return any(
        line.strip() and not line.strip().startswith((";", "#"))
        for line in lines
    )


def write_ini(folder: str, lines, encoding: str = "ansi") -> None:
    path = os.path.join(folder, INI_NAME)
    text = "\r\n".join(lines).strip("\r\n") + "\r\n"
    if encoding == "ansi":
        try:
            data = text.encode("mbcs")
        except (UnicodeError, LookupError) as exc:
            bad = []
            for char in text:
                try:
                    char.encode("mbcs")
                except (UnicodeError, LookupError):
                    if char not in bad and not char.isspace():
                        bad.append(char)
            raise ValueError(
                "含有系统 ANSI 代码页无法表示的字符："
                + (" ".join(bad) if bad else "(未知字符)")
                + "。请改用更常见的字符，或在编码下拉里改选 UTF-16LE。"
            ) from exc
    else:
        data = b"\xff\xfe" + text.encode("utf-16-le")
    # 和 Windows 自建的 desktop.ini 完全一致：A + HIDDEN + SYSTEM（0x26），
    # 这样它不会作为普通文件出现在文件夹里；仍可用完整路径在记事本里直接编辑。
    # 注意：Python 的 open(..., "wb") 覆盖带 H/S 属性的文件会被拒绝，
    # 所以写入前临时清掉属性，写完再设回标准属性（保留原有的其它可设置位）。
    previous = 0
    if os.path.isfile(path):
        try:
            previous = get_attrs(path) & ATTR_SETTABLE
            set_attr(path, A_HIDDEN | A_SYSTEM | A_READONLY, False)
        except OSError:
            previous = 0
    with open(path, "wb") as handle:
        handle.write(data)
    target = (previous | INI_STANDARD_ATTRS) & ~A_READONLY & 0xFFFFFFFF
    try:
        kernel32.SetFileAttributesW(os.path.abspath(path), target)
    except OSError:
        pass


def delete_ini(folder: str) -> None:
    path = os.path.join(folder, INI_NAME)
    if not os.path.isfile(path):
        return
    set_attr(path, A_HIDDEN | A_SYSTEM | A_READONLY, False)
    os.remove(path)


def normalize_ini_attrs(folder: str) -> int:
    """把已有的 desktop.ini 属性修正为系统标准（A + H + S），返回新属性值。"""
    path = os.path.join(folder, INI_NAME)
    if not os.path.isfile(path):
        raise ValueError("这个文件夹里还没有 desktop.ini")
    cur = get_attrs(path) & ATTR_SETTABLE
    target = (cur | INI_STANDARD_ATTRS) & ~A_READONLY & 0xFFFFFFFF
    if not kernel32.SetFileAttributesW(os.path.abspath(path), target):
        raise win_error()
    notify_folder(folder)
    return int(get_attrs(path))


def folder_has_shell_flag(folder: str) -> bool:
    """资源管理器只在带 READONLY 或 SYSTEM 标志的文件夹上解析 desktop.ini。"""
    return bool(get_attrs(folder) & (A_READONLY | A_SYSTEM))


def ensure_folder_system(folder: str) -> bool:
    """补上系统文件夹标志。按 Windows 的 PathMakeSystemFolder 做法用 READONLY，
    与用户手工设置的文件夹（如 D:\\Contact 的 +R）完全一致；已有 +S 也照样有效。"""
    if not folder_has_shell_flag(folder):
        set_attr(folder, A_READONLY, True)
        return True
    return False


def apply_localized_name(folder: str, name: str, encoding: str = "ansi") -> str:
    if not os.path.isdir(folder):
        raise ValueError("请先选择一个存在的文件夹")
    name = name.strip()
    if not name:
        raise ValueError("外显名不能为空")
    if "\n" in name or "\r" in name:
        raise ValueError("外显名不能包含换行")
    lines, _ = read_ini(folder)
    lines = ini_set(lines, SECTION, "LocalizedResourceName", name)
    write_ini(folder, lines, encoding)
    changed = ensure_folder_system(folder)
    notify_folder(folder)
    return ("已设置外显名；并已给文件夹加上 READONLY 系统文件夹标志" if changed
            else "已设置外显名（文件夹本来就带 READONLY / SYSTEM 标志）")


def _existing_encoding_key(folder: str) -> str:
    """改写已有 desktop.ini 时沿用它的编码，避免 UTF-16LE 文件被悄悄改回 ANSI。"""
    _, enc = read_ini(folder)
    return "utf16" if (enc or "").upper().startswith("UTF-16") else "ansi"


def clear_localized_name(folder: str) -> str:
    lines, _ = read_ini(folder)
    if ini_get(lines, SECTION, "LocalizedResourceName") is None:
        return "这个文件夹没有设置外显名，无需取消"
    lines = _drop_empty_section(
        ini_set(lines, SECTION, "LocalizedResourceName", None), SECTION
    )
    removed_ini = False
    if _has_content(lines):
        write_ini(folder, lines, _existing_encoding_key(folder))
    else:
        delete_ini(folder)
        removed_ini = True
    notify_folder(folder)
    if removed_ini:
        still = folder_has_shell_flag(folder)
        return ("已取消外显名并删除 desktop.ini" +
                ("；文件夹仍带 R/S 系统文件夹标志，需要的话点 [-R] / [-S]" if still else ""))
    return "已取消外显名（desktop.ini 中还有图标 / 悬浮提示等设置，已保留）"


def split_icon_resource(value: str):
    match = re.match(r"^(.*?),\s*(-?\d+)\s*$", value.strip())
    if match and match.group(1):
        return match.group(1), int(match.group(2))
    return value.strip(), 0


def apply_icon(folder: str, icon_path: str, index: int,
               encoding: str = "ansi") -> str:
    if not os.path.isdir(folder):
        raise ValueError("请先选择一个存在的文件夹")
    icon_path = icon_path.strip().strip('"')
    if not icon_path:
        raise ValueError("请选择图标文件")
    if not os.path.exists(icon_path):
        raise ValueError(f"图标文件不存在：{icon_path}")
    lines, _ = read_ini(folder)
    lines = ini_set(lines, SECTION, "IconResource",
                    f"{os.path.abspath(icon_path)},{int(index)}")
    write_ini(folder, lines, encoding)
    changed = ensure_folder_system(folder)
    notify_folder(folder)
    return ("已设置图标；并已给文件夹加上 READONLY 系统文件夹标志" if changed
            else "已设置图标（文件夹本来就带 READONLY / SYSTEM 标志）")


def clear_icon(folder: str) -> str:
    lines, _ = read_ini(folder)
    if ini_get(lines, SECTION, "IconResource") is None:
        return "这个文件夹没有设置自定义图标，无需清除"
    lines = _drop_empty_section(
        ini_set(lines, SECTION, "IconResource", None), SECTION
    )
    if _has_content(lines):
        write_ini(folder, lines, _existing_encoding_key(folder))
        removed = False
    else:
        delete_ini(folder)
        removed = True
    notify_folder(folder)
    return "已清除图标" + ("，并删除 desktop.ini" if removed else "")


# ---------------------------------------------------------------------------
# 链接角落图标（角标）：为三种链接方式各生成一种配色的角标
# ---------------------------------------------------------------------------

BADGE_ASSET_FILES = {
    "symlink": "link_badge_symlink.ico",
    "junction": "link_badge_junction.ico",
    "hardlink": "link_badge_hardlink.ico",
}


def _hex_to_rgb(text):
    text = str(text).lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _shade(rgb, delta):
    return tuple(max(0, min(255, c + delta)) for c in rgb)


def _draw_folder_base(draw, size):
    """简洁的文件夹剪影（开口朝前），作为角标底图。"""
    folder = (91, 122, 156)
    dark = _shade(folder, -28)
    light = _shade(folder, 26)
    draw.rounded_rectangle([int(size * 0.10), int(size * 0.20),
                            int(size * 0.92), int(size * 0.86)],
                           radius=int(size * 0.07), fill=dark)
    draw.rounded_rectangle([int(size * 0.10), int(size * 0.34),
                            int(size * 0.92), int(size * 0.86)],
                           radius=int(size * 0.07), fill=folder)
    draw.rounded_rectangle([int(size * 0.10), int(size * 0.12),
                            int(size * 0.40), int(size * 0.36)],
                           radius=int(size * 0.05), fill=dark)
    draw.line([int(size * 0.15), int(size * 0.42),
               int(size * 0.88), int(size * 0.42)],
              fill=light, width=max(2, int(size * 0.012)))


def _make_default_badge(kind):
    """生成 256x256 RGBA 图标：文件夹底图 + 右下角色调角标。返回 PIL Image。"""
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    tint, glyph = BADGE_COLORS.get(kind, ("#4c9aff", "?"))
    tint_rgb = _hex_to_rgb(tint)
    _draw_folder_base(draw, size)
    # 右下角角标
    badge = int(size * 0.42)
    bx = size - badge - int(size * 0.05)
    by = size - badge - int(size * 0.05)
    draw.rounded_rectangle([bx, by, bx + badge, by + badge],
                           radius=int(badge * 0.30), fill=tint_rgb)
    draw.rounded_rectangle([bx, by, bx + badge, by + badge],
                           radius=int(badge * 0.30), outline=(255, 255, 255),
                           width=max(2, int(size * 0.014)))
    # 类型字母
    fsize = int(badge * 0.60)
    try:
        font = ImageFont.truetype("msyh.ttc", fsize)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", fsize)
        except Exception:
            font = ImageFont.load_default()
    try:
        tw = draw.textlength(glyph, font=font)
    except Exception:
        tw = fsize * 0.6
    draw.text((bx + (badge - tw) / 2, by + (badge - fsize) / 2 - fsize * 0.06),
              glyph, fill=(255, 255, 255), font=font)
    return img


def _badge_asset_path(kind):
    return os.path.join(app_dir(), "ico",
                        BADGE_ASSET_FILES.get(kind, f"link_badge_{kind}.ico"))


def _ensure_badge_assets():
    """在 exe 目录的 ico/ 子目录生成三种默认角标 .ico（只生成一次），返回 kind -> 路径。"""
    out = {}
    ico_dir = os.path.join(app_dir(), "ico")
    try:
        os.makedirs(ico_dir, exist_ok=True)
    except Exception:
        pass
    for kind in ("symlink", "junction", "hardlink"):
        path = _badge_asset_path(kind)
        out[kind] = path
        if HAS_PIL and not os.path.exists(path):
            try:
                _make_default_badge(kind).save(path, format="ICO")
            except Exception:
                pass
    return out


def _badge_icon_for(kind, custom_map):
    """返回该方式实际使用的角标图标路径：有自定义就用自定义，否则用默认资源。"""
    custom = (custom_map or {}).get(kind, "")
    if custom and os.path.exists(custom):
        return custom
    return _badge_asset_path(kind)


def apply_infotip(folder: str, tip: str, encoding: str = "ansi") -> str:
    """在 [.ShellClassInfo] 里写 InfoTip=，实现鼠标悬停提示。"""
    if not os.path.isdir(folder):
        raise ValueError("请先选择一个存在的文件夹")
    tip = tip.strip()
    if not tip:
        raise ValueError("悬浮提示不能为空；要删除请点「清除悬浮提示」")
    if "\n" in tip or "\r" in tip:
        raise ValueError("InfoTip 只能是单行文本，请去掉换行")
    lines, _ = read_ini(folder)
    lines = ini_set(lines, SECTION, "InfoTip", tip)
    write_ini(folder, lines, encoding)
    changed = ensure_folder_system(folder)
    notify_folder(folder)
    return ("已设置悬浮提示 InfoTip；并已给文件夹加上 READONLY 系统文件夹标志" if changed
            else "已设置悬浮提示 InfoTip（文件夹本来就带 READONLY / SYSTEM 标志）")


def clear_infotip(folder: str) -> str:
    lines, _ = read_ini(folder)
    if ini_get(lines, SECTION, "InfoTip") is None:
        return "这个文件夹没有设置悬浮提示，无需清除"
    lines = _drop_empty_section(
        ini_set(lines, SECTION, "InfoTip", None), SECTION
    )
    if _has_content(lines):
        write_ini(folder, lines, _existing_encoding_key(folder))
        removed = False
    else:
        delete_ini(folder)
        removed = True
    notify_folder(folder)
    return "已清除悬浮提示" + ("，并删除 desktop.ini" if removed else "")


def read_folder_info(folder: str):
    lines, enc = read_ini(folder)
    ini_path = os.path.join(folder, INI_NAME)
    return {
        "attrs": get_attrs(folder),
        "ini_exists": enc is not None,
        "ini_encoding": enc,
        "ini_attrs": get_attrs(ini_path) if enc is not None else None,
        "name": ini_get(lines, SECTION, "LocalizedResourceName"),
        "icon": ini_get(lines, SECTION, "IconResource"),
        "tip": ini_get(lines, SECTION, "InfoTip"),
    }


def describe_attrs(attrs: int) -> str:
    """简短的属性字母串，例如 A H S，便于和 attrib 命令对照。"""
    letters = []
    for bit, letter in ((A_READONLY, "R"), (A_HIDDEN, "H"), (A_SYSTEM, "S"),
                        (A_DIRECTORY, "D"), (A_ARCHIVE, "A"),
                        (A_REPARSE_POINT, "L"), (A_COMPRESSED, "C"),
                        (A_ENCRYPTED, "E"), (A_NOT_INDEXED, "I")):
        if attrs & bit:
            letters.append(letter)
    return " ".join(letters) if letters else "普通"


def open_ini_in_editor(folder: str) -> str:
    """沿用手动编辑习惯：用系统默认编辑器（记事本）直接打开 desktop.ini。"""
    if not os.path.isdir(folder):
        raise ValueError("请先选择一个存在的文件夹")
    path = os.path.join(folder, INI_NAME)
    created = False
    if not os.path.isfile(path):
        write_ini(folder, [f"[{SECTION}]"], "ansi")
        created = True
    ensure_folder_system(folder)
    notify_folder(folder)
    os.startfile(path)
    if created:
        return ("当前还没有 desktop.ini，已新建一个 ANSI 空文件（A+H+S 隐藏系统属性）并用记事本打开；"
                "可写 LocalizedResourceName=社交 或 InfoTip=说明文字，保存后点「刷新状态」。")
    return ("已用记事本打开 desktop.ini（隐藏系统文件，需输入完整路径才能看到）；"
            "保存后点「刷新状态」即可读到新内容；若属性被编辑器改回普通文件，点「修正 desktop.ini 属性」。")


# ---------------------------------------------------------------------------
# 链接
# ---------------------------------------------------------------------------

def create_symlink(target: str, link: str, is_dir: bool) -> None:
    flags = (SYMBOLIC_LINK_FLAG_DIRECTORY if is_dir else 0) | \
            SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED
    ok = kernel32.CreateSymbolicLinkW(link, target, flags)
    if not ok:
        err = ctypes.get_last_error()
        # 老系统不认识 ALLOW_UNPRIVILEGED_CREATE 标志时回退
        if err == ERROR_INVALID_PARAMETER:
            ok = kernel32.CreateSymbolicLinkW(link, target, 1 if is_dir else 0)
            if ok:
                return
            err = ctypes.get_last_error()
        if err == ERROR_PRIVILEGE_NOT_HELD:
            raise PermissionError(
                "创建符号链接需要管理员权限（或开启 Windows 开发者模式）。"
                "可点击右上角「以管理员重启」。"
            )
        raise ctypes.WinError(err)


def create_hardlink(target: str, link: str) -> None:
    if not kernel32.CreateHardLinkW(link, target, None):
        raise win_error()


def create_junction(target: str, link: str) -> None:
    # mklink 是 cmd.exe 内建命令，纯 API 创建 junction 需要自造 REPARSE_DATA_BUFFER
    target = os.path.abspath(target)
    command = f'cmd /d /c mklink /J "{link}" "{target}"'
    done = subprocess.run(
        command, capture_output=True, text=True,
        encoding="mbcs", errors="replace", timeout=30,
        creationflags=CREATE_NO_WINDOW
    )
    if done.returncode != 0 or not os.path.exists(link):
        detail = (done.stdout or "").strip() or (done.stderr or "").strip()
        raise RuntimeError(f"mklink /J 失败（返回码 {done.returncode}）：{detail}")


def create_shortcut(target: str, link: str) -> None:
    if not link.lower().endswith(".lnk"):
        link += ".lnk"
    esc = lambda value: value.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{esc(link)}');"
        f"$s.TargetPath='{esc(target)}';"
        "$s.Save()"
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW
    )
    if done.returncode != 0 or not os.path.isfile(link):
        raise RuntimeError(
            "PowerShell 创建快捷方式失败，请确认系统允许运行 PowerShell 脚本"
        )


def inspect_path(path: str) -> str:
    """返回可读的路径类型 / 链接信息。"""
    if not os.path.exists(path) and not os.path.islink(path):
        return "路径不存在"
    attrs = get_attrs(path)
    stat = os.lstat(path)
    parts = []
    if attrs & A_REPARSE_POINT:
        tag = getattr(stat, "st_reparse_tag", 0)
        if tag == IO_REPARSE_TAG_SYMLINK:
            kind = "符号链接 Symbolic Link"
        elif tag == IO_REPARSE_TAG_MOUNT_POINT:
            kind = "目录联接 Junction"
        else:
            kind = f"其它重解析点 (tag 0x{tag:08X})"
        parts.append(kind)
        try:
            parts.append("指向：" + os.readlink(path))
        except OSError:
            pass
    else:
        parts.append("普通目录" if os.path.isdir(path) else "普通文件")
        nlink = getattr(stat, "st_nlink", 1)
        if not os.path.isdir(path) and nlink > 1:
            parts.append(f"检测到 {nlink} 个硬链接指向同一份数据")
    return "；".join(parts)


LINK_TYPES = ("symlink", "junction", "hardlink", "shortcut")
# 速查表里只列这三种真正的文件系统链接（快捷方式不是链接，已从表里去掉）
TABLE_TYPES = ("symlink", "junction", "hardlink")

COMPARE_COLS = ("链接方式", "适用对象", "跨盘 / 跨卷")
COMPARE_WEIGHTS = (2, 2, 3)

LINK_INFO = {
    "symlink": {
        "name": "符号链接",
        "en": "Symbolic Link",
        "short": "文件/目录 · 可跨盘 · 通常需管理员",
        "row": ("符号链接", "文件 + 文件夹", "支持，含网络 UNC"),
        "detail": [
            ("是什么", "一个带「指针」的特殊目录项，访问链接就等于访问目标本身，资源管理器里带小箭头。"),
            ("适用范围", "文件和文件夹都可以；可以跨盘符、跨卷，甚至可以指向网络 UNC 路径。"),
            ("管理员权限", "通常需要管理员权限；Windows 10/11 开启「开发者模式」后普通用户也可创建。"),
            ("修改同步", "两边访问的是同一份数据，任何一边改动，另一边立刻看到；不是复制，不额外占空间。"),
            ("目标不见了", "允许指向不存在的路径，链接会变成「失效链接」；删除链接不会删掉目标。"),
            ("删掉链接", "只删掉这个指针，目标与其中的内容完全不受影响。"),
            ("注意", "部分老程序会跟随链接、部分会当成普通文件；移动目标后链接不会自动更新。"),
            ("适用建议", "要在不同磁盘之间、或让各类程序都当成真实文件 / 文件夹访问时选它；没有管理员权限就先开「开发者模式」，或用「以管理员启动」。"),
        ],
    },
    "junction": {
        "name": "目录联接",
        "en": "Junction / Mount Point",
        "short": "仅目录 · 可跨本地盘 · 无需管理员",
        "row": ("目录联接", "仅文件夹", "本地盘支持，不支持 UNC"),
        "detail": [
            ("是什么", "NTFS 重解析点，把一个文件夹「挂」到另一个位置，对绝大多数程序看起来就是普通文件夹。"),
            ("适用范围", "只能对文件夹；可以跨本地磁盘，但不能指向网络 UNC 路径。"),
            ("管理员权限", "不需要管理员，普通用户即可创建，兼容性比符号链接更好。"),
            ("修改同步", "两边是同一个目录，增删改文件互相立刻可见，不占双倍空间。"),
            ("目标不见了", "目标必须是已存在的本地绝对路径，不能指向不存在的东西。"),
            ("删掉链接", "删除联接本身不会删目标内容；旧版资源管理器曾有过误删目标的坑，删除前确认选中的是链接。"),
            ("适用建议", "本机不同磁盘之间给文件夹做「分身」的首选：不用管理员、兼容性最好，只想让另一个位置看到同一个目录时用它。"),
        ],
    },
    "hardlink": {
        "name": "硬链接",
        "en": "Hard Link",
        "short": "仅文件 · 只能同盘同分区 · 无需管理员",
        "row": ("硬链接", "仅文件", "不支持，须同一分区"),
        "detail": [
            ("是什么", "给同一份磁盘数据再加一个文件名。两个名字地位完全平等，没有「源」和「链接」之分。"),
            ("适用范围", "只能对文件，不能对文件夹；只能在同一个 NTFS 分区内创建，不能跨盘。"),
            ("管理员权限", "不需要管理员，普通用户即可创建。"),
            ("修改同步", "改哪一边都是改同一份数据，立刻同步；不额外占用磁盘空间。"),
            ("目标不见了", "不允许指向不存在的文件，源文件必须先存在。"),
            ("删掉链接", "删掉其中一个名字，数据不会被删，另一个名字照常可用（引用计数减一）。"),
            ("注意", "有些编辑器保存时是「写临时文件再替换」，这样会断开硬链接。"),
            ("适用建议", "同一个分区里给文件加第二个名字、还想省空间（如备份 / 同步脚本）时用它；需要长期同步又怕编辑器断开链接，就改用符号链接或联接。"),
        ],
    },
    "shortcut": {
        "name": "快捷方式",
        "en": ".lnk",
        "short": "文件/目录/网址 · 任意位置 · 非真链接",
        "row": ("快捷方式", "文件/文件夹/程序/网址", "支持，含网络路径"),
        "detail": [
            ("是什么", "一个记录目标路径的普通 .lnk 小文件，不是文件系统层面的链接。"),
            ("适用范围", "文件、文件夹、程序、网址都能指向；可放在任意位置，包括网络路径。"),
            ("管理员权限", "不需要管理员，所有人都能建。"),
            ("修改同步", "不适用：资源管理器双击会跳转，但普通程序读到的是 .lnk 文件本身，不会自动变成目标。"),
            ("目标不见了", "允许指向不存在的路径，双击只会提示找不到目标。"),
            ("删掉链接", "只删掉这个 .lnk 文件，目标不受影响。"),
            ("适用建议", "只想给人「点一下打开」用快捷方式；想让程序、同步工具真的看到同一份数据，用前三种。"),
        ],
    },
}


def create_link(kind: str, target: str, link: str):
    """创建链接，返回 (实际链接路径, 结果描述)。"""
    target = os.path.abspath(target.strip().strip('"'))
    link = os.path.abspath(link.strip().strip('"'))
    if not os.path.exists(target):
        raise ValueError(f"源对象不存在：{target}")
    if kind == "shortcut":
        if not link.lower().endswith(".lnk"):
            link += ".lnk"
    if os.path.exists(link) or os.path.islink(link):
        raise ValueError(f"链接位置已经存在，请换一个名字：{link}")
    parent = os.path.dirname(link)
    if not os.path.isdir(parent):
        raise ValueError(f"链接所在的父文件夹不存在：{parent}")

    is_dir = os.path.isdir(target)
    if kind == "junction":
        if not is_dir:
            raise ValueError("目录联接 Junction 只能用于文件夹，请改选符号链接")
        create_junction(target, link)
    elif kind == "hardlink":
        if is_dir:
            raise ValueError("硬链接只能用于文件，不能用于文件夹，请改选目录联接或符号链接")
        td, ld = os.path.splitdrive(target)[0].upper(), os.path.splitdrive(link)[0].upper()
        if td != ld:
            raise ValueError(
                f"硬链接只能在同一分区内创建：源在 {td}，链接位置在 {ld}"
            )
        create_hardlink(target, link)
    elif kind == "symlink":
        create_symlink(target, link, is_dir)
    elif kind == "shortcut":
        create_shortcut(target, link)
    else:
        raise ValueError("未知的链接方式")

    return link, inspect_path(link)


# ---------------------------------------------------------------------------
# 链接记录：默认只记本软件创建/修改过的，不自动扫盘
# ---------------------------------------------------------------------------

LINKS_FILE = "folder_studio_links.json"
LINK_KIND_NAMES = {
    "symlink": "符号链接", "junction": "目录联接",
    "hardlink": "硬链接", "shortcut": "快捷方式",
}


def load_link_records():
    """读取持久化记录；从不扫描磁盘，避免启动时卡住。"""
    try:
        with open(os.path.join(app_dir(), LINKS_FILE), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        rows = data.get("links", []) if isinstance(data, dict) else []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            link = str(row.get("link") or "").strip()
            if not link:
                continue
            out.append({
                "kind": str(row.get("kind") or "symlink"),
                "target": str(row.get("target") or ""),
                "link": os.path.abspath(link),
                "origin": str(row.get("origin") or "本软件创建"),
                "time": str(row.get("time") or ""),
            })
        return out
    except Exception:
        return []


def save_link_records(records):
    try:
        with open(os.path.join(app_dir(), LINKS_FILE), "w", encoding="utf-8") as handle:
            json.dump({"links": records}, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


def new_link_record(kind, target, link, origin="本软件创建"):
    return {
        "kind": kind,
        "target": target,
        "link": os.path.abspath(link),
        "origin": origin,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def merge_link_records(records, found):
    """按链接路径去重合并，返回 (列表, 新增数量)。"""
    index = {os.path.normcase(r["link"]): i for i, r in enumerate(records)}
    added = 0
    for rec in found:
        key = os.path.normcase(rec["link"])
        if key in index:
            old = records[index[key]]
            if rec.get("target") and rec["target"] != old.get("target"):
                old["target"] = rec["target"]
            old["kind"] = rec["kind"]
        else:
            index[key] = len(records)
            records.append(rec)
            added += 1
    return records, added


def normalize_reparse_target(value):
    """os.readlink 在 Windows 上会返回 \\?\\ 或 \\??\\ 前缀，转成普通路径。"""
    if not value:
        return ""
    for prefix in ("\\??\\", "\\\\?\\", "\\DosDevices\\"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.upper().startswith("UNC\\"):
        value = "\\\\" + value[4:]
    return os.path.normpath(value)


def resolve_shortcuts(paths):
    """一次外部调用批量解析 .lnk 的目标路径（中文按系统代码页解码）。"""
    paths = [p for p in paths if p][:200]
    if not paths:
        return {}
    arr = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$w=New-Object -ComObject WScript.Shell;"
        f"$paths=@({arr});"
        'foreach($p in $paths){$s=$w.CreateShortcut($p);'
        'Write-Output ($p + "`t" + $s.TargetPath)}'
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=40, creationflags=CREATE_NO_WINDOW
        )
        text = done.stdout.decode("mbcs", errors="replace")
        result = {}
        for line in text.splitlines():
            if "\t" in line:
                key, value = line.split("\t", 1)
                result[os.path.normcase(key.strip())] = value.strip()
        return result
    except Exception:
        return {}


def scan_links(root, max_depth=2, max_entries=12000, skip=(),
               include_shortcuts=False):
    """扫描选定路径下的链接：重解析点 / .lnk / 多名字硬链接。
    跳过重解析点目录避免死循环或跨盘跳转，并限制层数与条目数。
    skip：跳过名单（文件夹绝对路径），名单内的文件夹不记录、不深入。
    include_shortcuts：False 时只找符号链接 / 目录联接 / 硬链接三种。"""
    skip_set = set()
    for item in skip or ():
        try:
            skip_set.add(os.path.normcase(os.path.abspath(str(item))))
        except Exception:
            continue
    start = os.path.abspath(root)
    if os.path.normcase(start) in skip_set:
        return [], 0
    found, shortcuts, count = [], [], 0
    stack = [(start, 0)]
    while stack and count <= max_entries:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            count += 1
            if count > max_entries:
                break
            try:
                stat = entry.stat(follow_symlinks=False)
                attrs = getattr(stat, "st_file_attributes", 0)
                tag = getattr(stat, "st_reparse_tag", 0)
            except OSError:
                continue
            path = os.path.abspath(entry.path)
            if os.path.normcase(path) in skip_set:
                continue  # 跳过名单内的文件夹：不记录也不进入
            if attrs & A_REPARSE_POINT:
                if tag == IO_REPARSE_TAG_SYMLINK:
                    kind = "symlink"
                elif tag == IO_REPARSE_TAG_MOUNT_POINT:
                    kind = "junction"
                else:
                    continue  # 云占位、去重点等不算用户链接
                try:
                    target = normalize_reparse_target(os.readlink(path))
                except OSError:
                    target = ""
                found.append(new_link_record(kind, target, path, "扫描发现"))
                continue  # 不进入链接目录
            if not (attrs & A_DIRECTORY):
                if path.lower().endswith(".lnk"):
                    if not include_shortcuts:
                        continue
                    shortcuts.append(path)
                    found.append(new_link_record("shortcut", "", path, "扫描发现"))
                elif getattr(stat, "st_nlink", 1) > 1:
                    found.append(new_link_record("hardlink", "", path, "扫描发现"))
                continue
            if depth < max_depth:
                stack.append((path, depth + 1))
    if shortcuts:
        resolved = resolve_shortcuts(shortcuts)
        for rec in found:
            if rec["kind"] == "shortcut":
                rec["target"] = resolved.get(os.path.normcase(rec["link"]), "")
    return found, count


def refresh_record_target(rec):
    """刷新时从链接本身重新读目标，避免目标路径过期。"""
    link, kind = rec["link"], rec["kind"]
    if not os.path.lexists(link):
        return
    if kind in ("symlink", "junction"):
        try:
            target = normalize_reparse_target(os.readlink(link))
            if target:
                rec["target"] = target
        except OSError:
            pass
    elif kind == "shortcut":
        target = resolve_shortcuts([link]).get(os.path.normcase(link), "")
        if target:
            rec["target"] = target


def link_status(rec):
    """只检查这一条记录的两个路径，不做任何盘级扫描。"""
    link, kind = rec.get("link", ""), rec.get("kind", "")
    if not link:
        return "记录不完整"
    if not os.path.lexists(link):
        return "链接已不存在"
    try:
        stat = os.lstat(link)
        attrs = getattr(stat, "st_file_attributes", 0)
        tag = getattr(stat, "st_reparse_tag", 0)
    except OSError:
        return "无法读取"
    if kind == "shortcut":
        if not (os.path.isfile(link) and link.lower().endswith(".lnk")):
            return "已不是快捷方式"
    elif kind == "hardlink":
        if getattr(stat, "st_nlink", 1) <= 1:
            return "硬链接已断开"
    elif kind == "symlink":
        if not (attrs & A_REPARSE_POINT and tag == IO_REPARSE_TAG_SYMLINK):
            return "已不是符号链接"
    elif kind == "junction":
        if not (attrs & A_REPARSE_POINT and tag == IO_REPARSE_TAG_MOUNT_POINT):
            return "已不是目录联接"
    target = rec.get("target", "")
    if target and not os.path.exists(target):
        return "已失效"
    return "正常"


def record_name(rec):
    base = rec.get("target") or rec.get("link") or ""
    return os.path.basename(os.path.normpath(base)) or "(未命名)"


def record_format(rec):
    """格式列：文件夹 / 扩展名（exe、zip…），取源（目标）优先，链接位置兜底。"""
    for path in (rec.get("target") or "", rec.get("link") or ""):
        if not path:
            continue
        try:
            if os.path.isdir(path):
                return "文件夹"
        except OSError:
            continue
    base = rec.get("target") or rec.get("link") or ""
    ext = os.path.splitext(base)[1].lower().lstrip(".")
    return ext or "无"


def record_label(rec, status=None):
    kind = LINK_KIND_NAMES.get(rec.get("kind"), rec.get("kind", "?"))
    return f"{record_name(rec)}   ·   {kind}   ·   {status or link_status(rec)}"


def remove_link_file(rec):
    """只删除链接本身。目录型用 RemoveDirectoryW，文件型用 DeleteFileW。"""
    link = rec["link"]
    if not os.path.lexists(link):
        return "链接位置已不存在，已从列表移除记录（磁盘未改动）"
    stat = os.lstat(link)
    is_dir_link = bool(getattr(stat, "st_file_attributes", 0) & A_DIRECTORY)
    if is_dir_link and rec.get("kind") in ("symlink", "junction"):
        ok = kernel32.RemoveDirectoryW(os.path.abspath(link))
    else:
        ok = kernel32.DeleteFileW(os.path.abspath(link))
    if not ok:
        raise win_error()
    try:
        notify_folder(os.path.dirname(link))
    except Exception:
        pass
    if os.path.lexists(link):
        raise RuntimeError("链接仍然存在，未删除，请检查权限")
    return "已解除链接：只删除了链接本身，主文件夹 / 目标内容保留"


# ---------------------------------------------------------------------------
# 界面配色
# ---------------------------------------------------------------------------

BG = "#17191d"
CARD = "#21252b"
CARD_ALT = "#1c2026"
ELEV = "#2b313a"
BORDER = "#343b45"
FG = "#e8ecf1"
MUTED = "#9aa5b1"
ACCENT = "#4c9aff"
ACCENT_DARK = "#2f6fc4"
GREEN = "#57c98a"
AMBER = "#e3b341"
RED = "#f0766b"
CYAN = "#5ec8d8"

# 版本号（GitHub Release 打 v{APP_VERSION} 标签）：标题栏与底栏右下角显示
APP_VERSION = "2.3"

# 构建时间（重新打包 exe 时改这里）：底栏右下角显示「构建于 yyyy/mm/dd」
BUILD_DATE = "2026/11/13"

# 三种链接方式的角落角标配色：刻意避开快捷方式(.lnk)的小箭头蓝白色系，
# 彼此也互不干扰，便于在资源管理器里一眼区分。
#   symlink  -> 蓝（与界面主色一致）
#   junction -> 绿
#   hardlink -> 琥珀
BADGE_COLORS = {
    "symlink": ("#4c9aff", "S"),
    "junction": ("#57c98a", "J"),
    "hardlink": ("#e3b341", "H"),
}

# 字体用「命名字体对象」而非静态元组：改 size 后所有引用它的控件会实时更新。
FONT_SPECS = {
    "FONT": ("Microsoft YaHei UI", 10, "normal"),
    "FONT_SM": ("Microsoft YaHei UI", 9, "normal"),
    "FONT_B": ("Microsoft YaHei UI", 10, "bold"),
    "FONT_TITLE": ("Microsoft YaHei UI", 16, "bold"),
    "FONT_SUB": ("Microsoft YaHei UI", 9, "normal"),
    "FONT_MONO": ("Consolas", 9, "normal"),
    "FONT_H2": ("Microsoft YaHei UI", 12, "bold"),
    "FONT_TAG": ("Microsoft YaHei UI", 9, "bold"),
}
FONTS = {}
FONT = FONT_SM = FONT_B = FONT_TITLE = FONT_SUB = FONT_MONO = FONT_H2 = FONT_TAG = None


def init_fonts(root):
    global FONT, FONT_SM, FONT_B, FONT_TITLE, FONT_SUB, FONT_MONO, FONT_H2, FONT_TAG
    for name, (family, size, weight) in FONT_SPECS.items():
        FONTS[name] = tkfont.Font(root, family=family, size=size, weight=weight)
    FONT, FONT_SM, FONT_B = FONTS["FONT"], FONTS["FONT_SM"], FONTS["FONT_B"]
    FONT_TITLE, FONT_SUB = FONTS["FONT_TITLE"], FONTS["FONT_SUB"]
    FONT_MONO, FONT_H2, FONT_TAG = FONTS["FONT_MONO"], FONTS["FONT_H2"], FONTS["FONT_TAG"]


def apply_font_scale(scale):
    for name, (_family, base, _weight) in FONT_SPECS.items():
        FONTS[name].configure(size=max(6, int(round(base * scale))))
    return scale


# ---- 设置持久化（写到 exe / 脚本所在目录） ----

SETTINGS_FILE = "folder_studio_settings.json"


def parse_geometry(text):
    """把 '1040x740+40+30' 解析成 (w, h, x, y)，无效返回 None。"""
    match = re.match(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$", str(text or ""))
    if not match:
        return None
    try:
        w, h = int(match.group(1)), int(match.group(2))
        x = int(match.group(3).replace("+", ""))
        y = int(match.group(4).replace("+", ""))
    except ValueError:
        return None
    if w < 400 or h < 300:
        return None
    return w, h, x, y


def load_settings():
    data = {"scale": 1.0, "encoding": "ANSI", "geometry": "",
            "scan_skip": [], "scan_shortcuts": False,
            "log_height": 130, "badge_default": "add",
            "badge_custom": {"symlink": "", "junction": "", "hardlink": ""}}
    try:
        with open(os.path.join(app_dir(), SETTINGS_FILE), "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict):
            data.update({k: v for k, v in saved.items() if k in data})
    except Exception:
        pass
    try:
        data["scale"] = min(2.0, max(0.6, float(data["scale"])))
    except (TypeError, ValueError):
        data["scale"] = 1.0
    if data["encoding"] not in ("ANSI", "UTF-16LE"):
        data["encoding"] = "ANSI"
    if not isinstance(data["geometry"], str):
        data["geometry"] = ""
    if not isinstance(data["scan_skip"], list):
        data["scan_skip"] = []
    seen, unique = set(), []
    for item in data["scan_skip"]:
        item = str(item).strip()
        if not item:
            continue
        key = os.path.normcase(os.path.abspath(item))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    data["scan_skip"] = unique
    data["scan_shortcuts"] = bool(data["scan_shortcuts"])
    if not isinstance(data["log_height"], int) or not (60 <= data["log_height"] <= 800):
        data["log_height"] = 130
    if data["badge_default"] not in ("add", "none", "restore"):
        data["badge_default"] = "add"
    custom = data.get("badge_custom")
    if not isinstance(custom, dict):
        custom = {}
    data["badge_custom"] = {
        kind: str(custom.get(kind) or "").strip()
        for kind in ("symlink", "junction", "hardlink")
    }
    return data


def save_settings(data):
    try:
        with open(os.path.join(app_dir(), SETTINGS_FILE), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


def make_card(parent, title, subtitle=None, bg=CARD, right=None):
    outer = tk.Frame(parent, bg=bg, highlightbackground=BORDER,
                     highlightthickness=1, padx=14, pady=11)
    head = tk.Frame(outer, bg=bg)
    head.pack(fill="x")
    # 右侧内容先入队，窄卡时优先保住它，标题 / 副标题让位
    if right is not None:
        try:
            right(head)
        except Exception:
            traceback.print_exc()
    tk.Label(head, text=title, bg=bg, fg=FG, font=FONT_B).pack(side="left")
    if subtitle:
        tk.Label(head, text=subtitle, bg=bg, fg=MUTED,
                 font=FONT_SUB).pack(side="left", padx=(10, 0))
    body = tk.Frame(outer, bg=bg)
    body.pack(fill="both", expand=True, pady=(10, 0))
    return outer, body


class HoverPopup:
    """深色多行悬浮说明：延迟出现、只在内容切换时重建，不抢焦点。"""

    def __init__(self, root):
        self.root = root
        self.win = None
        self._key = None
        self._show_job = None
        self._show_key = None
        self._hide_job = None

    def _cancel_show(self):
        if self._show_job is not None:
            try:
                self.root.after_cancel(self._show_job)
            except Exception:
                pass
            self._show_job = None
            self._show_key = None

    def _cancel_hide(self):
        if self._hide_job is not None:
            try:
                self.root.after_cancel(self._hide_job)
            except Exception:
                pass
            self._hide_job = None

    def hide(self):
        self._cancel_show()
        self._cancel_hide()
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None
        self._key = None

    def leave(self):
        """离开控件时延迟隐藏；同一行内移动会重新进入，不会闪。"""
        self._cancel_show()
        self._cancel_hide()
        self._hide_job = self.root.after(200, self.hide)

    def schedule(self, key, title, body, x, y, delay=260):
        self._cancel_hide()
        if self._key == key and self.win is not None:
            return
        if self._show_key == key and self._show_job is not None:
            return
        if self.win is not None:
            self.hide()
        self._cancel_show()
        self._show_key = key
        self._show_job = self.root.after(
            delay, lambda: self._show(key, title, body, x, y))

    def _show(self, key, title, body, x, y):
        self._show_job = None
        self._show_key = None
        self.hide()
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        win.configure(bg=BORDER)
        try:
            win.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        box = tk.Frame(win, bg=CARD, padx=14, pady=11,
                       highlightbackground=ACCENT, highlightthickness=1)
        box.pack(fill="both", expand=True)
        tk.Label(box, text=title, bg=CARD, fg=ACCENT, font=FONT_B,
                 anchor="w", justify="left").pack(fill="x")
        tk.Label(box, text=body, bg=CARD, fg=FG, font=FONT_SM,
                 anchor="w", justify="left", wraplength=540).pack(
                     fill="x", pady=(8, 0))
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px = max(8, min(x + 18, max(8, sw - w - 8)))
        py = max(8, min(y + 18, max(8, sh - h - 8)))
        win.wm_geometry(f"+{px}+{py}")
        self.win = win
        self._key = key


# ---------------------------------------------------------------------------
# 文件 / 文件夹拖放（WM_DROPFILES）
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
    wintypes.WPARAM, wintypes.LPARAM
)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _setup_drop_api():
    """绑定用到的 user32 / shell32 函数签名，失败返回 None。"""
    try:
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragAcceptFiles.restype = None
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                           wintypes.LPWSTR, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryPoint.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(_POINT)]
        shell32.DragQueryPoint.restype = wintypes.BOOL
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        shell32.DragFinish.restype = None
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        return setter
    except Exception:
        return None


def _drop_paths(handle):
    count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
    buffer = ctypes.create_unicode_buffer(32768)
    paths = []
    for index in range(min(count, 64)):
        if shell32.DragQueryFileW(handle, index, buffer, 32768):
            paths.append(buffer.value)
    return paths


def log_exception(note: str) -> None:
    """把异常写进 error.log —— 打包成 windowed exe 后没有控制台，只能靠日志。"""
    detail = traceback.format_exc()
    try:
        with open(os.path.join(app_dir(), "error.log"), "a", encoding="utf-8") as handle:
            handle.write(f"\n===== {datetime.datetime.now()} · {note} =====\n{detail}\n")
    except Exception:
        pass


def install_file_drop(root, on_drop):
    """给窗口挂上拖放支持。

    回调发生在 Windows 的消息处理里，那一刻绝对不能碰 Tk：
    这里只把路径存进队列，由主线程的定时器取出来处理，
    既避免重入 Tk 造成闪退，也让 Tk 侧的异常不至于打到窗口过程里。
    """
    setter = _setup_drop_api()
    if setter is None:
        return False

    keep = []
    pending = []
    DROP_DEBUG = []  # 开发调试：记录收到的拖放坐标

    def pump():
        # 在主线程里消费拖放队列
        while pending:
            item = pending.pop(0)
            try:
                on_drop(*item)
            except Exception:
                log_exception("处理拖放")
        try:
            root.after(90, pump)
        except Exception:
            pass

    def build_proc():
        holder = {}

        def handler(hwnd, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                try:
                    point = _POINT()
                    paths = _drop_paths(wparam)
                    shell32.DragQueryPoint(wparam, ctypes.byref(point))
                    shell32.DragFinish(wparam)
                    # 换算成屏幕坐标，Tk 侧就不用再猜是哪个窗口收到的消息
                    user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point))
                    if paths:
                        pending.append((paths, int(point.x), int(point.y)))
                        DROP_DEBUG.append((int(point.x), int(point.y), int(hwnd)))
                except Exception:
                    pass
                return 0
            old = holder.get("old")
            if not old:
                return 0
            return user32.CallWindowProcW(old, hwnd, msg, wparam, lparam)

        proc = WNDPROC(handler)
        holder["proc"] = proc
        return holder, proc

    handles, seen = [], set()
    hwnd = root.winfo_id()
    while hwnd and hwnd not in seen:
        seen.add(hwnd)
        handles.append(hwnd)
        hwnd = user32.GetParent(wintypes.HWND(hwnd))

    installed = 0
    for handle in handles:
        try:
            holder, proc = build_proc()
            old = setter(wintypes.HWND(handle), GWLP_WNDPROC,
                         ctypes.cast(proc, ctypes.c_void_p))
            if not old:
                continue
            holder["old"] = old
            keep.append(holder)
            shell32.DragAcceptFiles(wintypes.HWND(handle), True)
            installed += 1
        except Exception:
            continue
    root._drop_keep = keep  # 保住回调对象，避免被 GC 后崩溃
    root._drop_debug = DROP_DEBUG
    if installed:
        try:
            root.after(90, pump)
        except Exception:
            return False
    return installed > 0


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------

class FolderStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        init_fonts(self)
        self.settings = load_settings()
        apply_font_scale(self.settings["scale"])
        self.title(f"Folder Studio v{APP_VERSION} · 文件夹外显名 / 图标 / 链接工具")
        # 上次关闭时的位置与尺寸；没有记录或超出屏幕时用默认值
        self.geometry(self._initial_geometry())
        self.minsize(820, 540)
        self.resizable(True, True)  # 四个边角都能拖动调整大小
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.configure(bg=BG)
        self._setup_style()

        self.folder_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.tip_var = tk.StringVar()
        self.enc_var = tk.StringVar(value=self.settings["encoding"])
        self.zoom_var = tk.StringVar(value=f'{self.settings["scale"] * 100:.0f}')
        self.scan_shortcuts_var = tk.BooleanVar(
            value=self.settings["scan_shortcuts"])
        self.scan_skip = list(self.settings["scan_skip"])
        self.icon_var = tk.StringVar()
        self.icon_idx_var = tk.StringVar(value="0")
        self.src_var = tk.StringVar()
        self.dst_var = tk.StringVar()
        self.link_kind = "symlink"
        self.type_rows = {}
        self._loaded_folder = None
        self.hover = HoverPopup(self)
        self._pages = []
        self._tab_buttons = []
        self._current_tab = 0
        self.link_records = load_link_records()
        self._link_sort = {"col": "name", "reverse": False}
        self._link_map = {}
        self._scanning = False
        self._scan_result = None

        self._build_header()
        self._build_statusbar()
        self._build_log()

        # 固定尺寸的页签条（在 _build_header 里创建，与标题、权限按钮同一行）：
        # 切换只改颜色，不产生变大 / 缩小的视觉变化
        self.tab_pages = tk.Frame(self, bg=BG)
        self.tab_pages.pack(fill="both", expand=True, padx=14, pady=(4, 0))
        self.tab_pages.rowconfigure(0, weight=1)
        self.tab_pages.columnconfigure(0, weight=1)

        titles = ("外显名·图标·属性", "创建链接", "设置")
        pages = [self._scrollable_tab() for _ in titles]
        self._build_tab1(pages[0])
        self._build_tab2(pages[1])
        self._build_tab3(pages[2])
        for index, title in enumerate(titles):
            self._add_tab_button(index, title)
        self._select_tab(0)
        self._sync_encoding_label()
        self._refresh_link_table()
        # 统一接管滚轮：一个处理函数按指针位置决定滚哪一页，避免嵌套控件双重滚动
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

        if install_file_drop(self, self._on_drop):
            self.log("可以把文件 / 文件夹直接拖进窗口：拖到源对象框变成链接源，拖到路径框变成要设置的文件夹。", "hint")
        self._badge_assets = _ensure_badge_assets()
        self.log("就绪。先在「外显名」页选择一个文件夹，或切到「创建链接」页。", "ok")
        if not is_admin():
            self.log("当前为普通权限：硬链接 / 目录联接可用，符号链接通常需要管理员。", "warn")

    # ---------- 窗口位置记忆 ----------

    def _initial_geometry(self):
        """沿用上次关闭时的位置和大小；坐标超出屏幕就退回默认。"""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        saved = parse_geometry(self.settings.get("geometry"))
        if saved:
            width, height, x, y = saved
            width = min(width, max(760, screen_w - 20))
            height = min(height, max(500, screen_h - 80))
            # 至少留出 140px 在屏幕内，标题栏也不能跑到屏幕上方
            if -width + 140 <= x <= screen_w - 140 and 0 <= y <= screen_h - 80:
                return f"{width}x{height}+{x}+{y}"
        width = min(1040, max(760, screen_w - 60))
        height = min(740, max(500, screen_h - 120))
        return f"{width}x{height}+40+30"

    def _remember_geometry(self):
        try:
            if self.state() != "normal":
                return  # 最大化 / 最小化时不覆盖上次的普通尺寸
            self.settings["geometry"] = (
                f"{self.winfo_width()}x{self.winfo_height()}"
                f"+{self.winfo_x()}+{self.winfo_y()}")
            save_settings(self.settings)
        except Exception:
            pass

    def _on_close(self):
        self._remember_geometry()
        self.hover.hide()
        self.destroy()

    # ---------- 可滚动标签页 ----------

    def _scrollable_tab(self):
        """内容比窗口高时用滚轮查看，避免在小屏 / 高 DPI 下被裁掉。"""
        outer = tk.Frame(self.tab_pages, bg=BG)
        outer.grid(row=0, column=0, sticky="nsew")
        self._pages.append(outer)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        state = {"job": None}

        def _relayout():
            state["job"] = None
            if not canvas.winfo_exists():
                return
            need = inner.winfo_reqheight()
            # 内容放得下就隐藏滚动条（消除拉长窗口后「白条占满」），放不下才显示
            if need > canvas.winfo_height() + 2:
                if not scrollbar.winfo_ismapped():
                    scrollbar.pack(side="right", fill="y")
            else:
                if scrollbar.winfo_ismapped():
                    scrollbar.pack_forget()
            width = canvas.winfo_width()
            canvas.itemconfig(window_id, width=width, height=need)
            canvas.configure(scrollregion=(0, 0, width, need))

        def _schedule(_event=None):
            # 缩放 / 换行时只在停顿后重排一次，避免逐像素重排造成卡顿
            if state["job"]:
                try:
                    self.after_cancel(state["job"])
                except Exception:
                    pass
            state["job"] = self.after(40, _relayout)

        inner.bind("<Configure>", _schedule)
        canvas.bind("<Configure>", _schedule)
        outer._page_canvas = canvas  # 供外部精确定位这一页的滚动区
        outer._page_relayout = _relayout
        return inner

    def _relayout_tab(self, index):
        """手动触发某页的滚动条自适应（内容增删后调用）。"""
        if not (0 <= index < len(self._pages)):
            return
        page = self._pages[index]
        relayout = getattr(page, "_page_relayout", None)
        if relayout is not None:
            try:
                relayout()
            except Exception:
                pass

    # 自带滚动条的控件：滚轮交给它们自己处理，页面不再跟着滚
    SELF_SCROLLING = {"Treeview", "Text", "Listbox", "TCombobox", "TSpinbox"}

    def _on_mousewheel(self, event):
        """全局滚轮：只滚指针所在的页；指针在自带滚动的控件上时完全不干预。"""
        widget = getattr(event, "widget", None)
        if widget is None or isinstance(widget, str):
            return
        page = None
        depth = 0
        while widget is not None and depth < 40:
            depth += 1
            try:
                klass = widget.winfo_class()
            except Exception:
                klass = ""
            if klass in self.SELF_SCROLLING or getattr(widget, "_self_scroll", False):
                return
            canvas = getattr(widget, "_page_canvas", None)
            if canvas is not None:
                page = canvas
                break
            widget = getattr(widget, "master", None)
        if page is None:
            return
        try:
            if page.yview() == (0.0, 1.0):
                return
            delta = getattr(event, "delta", 0) or 0
            step = int(-delta / 120) or (1 if delta < 0 else -1)
            page.yview_scroll(step, "units")
        except Exception:
            pass

    def _add_tab_button(self, index, title):
        """固定像素宽高的页签，字体也不随缩放变化，切换时不会变大变小。"""
        frame = tk.Frame(self.tabbar, bg=CARD, width=146, height=32,
                         highlightbackground=BORDER, highlightthickness=1,
                         cursor="hand2")
        frame.pack(side="left", padx=(0, 6))
        frame.pack_propagate(False)
        label = tk.Label(frame, text=title, bg=CARD, fg=MUTED,
                         font=("Microsoft YaHei UI", 10, "bold"))
        label.pack(expand=True, fill="both")
        for widget in (frame, label):
            widget.bind("<Button-1>", lambda _e, i=index: self._select_tab(i))
        self._tab_buttons.append((frame, label))

    def _select_tab(self, index):
        self._current_tab = index
        self.hover.hide()
        for i, (frame, label) in enumerate(self._tab_buttons):
            active = i == index
            bg = ACCENT if active else CARD
            frame.configure(bg=bg,
                            highlightbackground=ACCENT if active else BORDER)
            label.configure(bg=bg, fg="#ffffff" if active else MUTED)
        self._pages[index].tkraise()
        if index == 2:
            self.after(60, self._update_skip_scrollbar)

    # ---------- 样式 ----------

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, font=FONT)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=FONT)
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background=CARD, foreground=MUTED,
                        padding=(18, 9), borderwidth=0, font=FONT_B)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT), ("active", ELEV)],
                  foreground=[("selected", "#ffffff"), ("active", FG)])
        style.configure("TEntry", fieldbackground="#111418", foreground=FG,
                        insertcolor=FG, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, padding=6)
        style.map("TEntry", bordercolor=[("focus", ACCENT)])
        style.configure("TButton", background=ELEV, foreground=FG,
                        bordercolor=BORDER, lightcolor=ELEV, darkcolor=ELEV,
                        padding=(11, 6), font=FONT)
        style.map("TButton",
                  background=[("active", "#3a424e"), ("disabled", CARD_ALT)],
                  foreground=[("disabled", "#5d6670")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        bordercolor=ACCENT, font=FONT_B)
        style.map("Accent.TButton",
                  background=[("active", ACCENT_DARK), ("disabled", "#33507a")],
                  foreground=[("disabled", "#8fa8c8")])
        style.configure("Danger.TButton", background="#4a2729", foreground="#ffb4ad",
                        bordercolor="#6a3538")
        style.map("Danger.TButton", background=[("active", "#5d3033")])
        # 紧凑小按钮：按钮多的一行放得下，也不裁字
        style.configure("Small.TButton", background=ELEV, foreground=FG,
                        bordercolor=BORDER, lightcolor=ELEV, darkcolor=ELEV,
                        padding=(7, 4), font=FONT_SM)
        style.map("Small.TButton",
                  background=[("active", "#3a424e"), ("disabled", CARD_ALT)],
                  foreground=[("disabled", "#5d6670")])
        style.configure("Accent.Small.TButton", background=ACCENT,
                        foreground="#ffffff", bordercolor=ACCENT, font=FONT_SM)
        style.map("Accent.Small.TButton",
                  background=[("active", ACCENT_DARK), ("disabled", "#33507a")],
                  foreground=[("disabled", "#8fa8c8")])
        style.configure("Danger.Small.TButton", background="#4a2729",
                        foreground="#ffb4ad", bordercolor="#6a3538", font=FONT_SM)
        style.map("Danger.Small.TButton", background=[("active", "#5d3033")])
        # 极小的按钮：日志「清空」之类只要一个小高度
        style.configure("Tiny.TButton", background=ELEV, foreground=FG,
                        bordercolor=BORDER, lightcolor=ELEV, darkcolor=ELEV,
                        padding=(7, 1), font=FONT_SM)
        style.map("Tiny.TButton",
                  background=[("active", "#3a424e"), ("disabled", CARD_ALT)],
                  foreground=[("disabled", "#5d6670")])
        style.configure("Tiny.Accent.TButton", background=ACCENT,
                        foreground="#ffffff", bordercolor=ACCENT, font=FONT_SM,
                        padding=(7, 1))
        style.map("Tiny.Accent.TButton",
                  background=[("active", ACCENT_DARK)])
        style.configure("TCombobox", fieldbackground="#111418", foreground=FG,
                        background=ELEV, arrowcolor=FG, bordercolor=BORDER,
                        padding=5)
        style.map("TCombobox", bordercolor=[("focus", ACCENT)])
        style.configure("TScrollbar", background=ELEV, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED)
        style.configure("Link.Treeview", background="#1b1f24",
                        fieldbackground="#1b1f24", foreground=FG,
                        bordercolor=BORDER, rowheight=26, font=FONT_SM,
                        borderwidth=0)
        style.configure("Link.Treeview.Heading", background=CARD_ALT,
                        foreground=MUTED, font=FONT_SM, relief="flat",
                        bordercolor=BORDER, padding=(6, 5))
        style.map("Link.Treeview.Heading",
                  background=[("active", ELEV)])
        style.map("Link.Treeview",
                  background=[("selected", "#1d3550")],
                  foreground=[("selected", "#ffffff")])
        self.option_add("*TCombobox*Listbox.background", ELEV)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.font", FONT)

    # ---------- 顶部 ----------

    def _build_header(self):
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x")

        # 左：标题（去掉副标题，缩短头部高度）
        tk.Label(bar, text="Folder Studio", bg=BG, fg=FG,
                 font=FONT_B).pack(side="left", padx=(14, 10), pady=(6, 6))

        # 中：页签（固定尺寸，切换只改颜色）
        self.tabbar = tk.Frame(bar, bg=BG)
        self.tabbar.pack(side="left", padx=(2, 0), pady=(4, 4))

        # 右：权限显示 + 两个按钮，与页签同一行
        right = tk.Frame(bar, bg=BG)
        right.pack(side="right", padx=(0, 14), pady=(4, 4))
        admin = is_admin()
        tk.Label(right, text="管理员权限" if admin else "普通权限",
                 bg="#173a28" if admin else "#4a3410",
                 fg=GREEN if admin else AMBER, font=FONT_SM,
                 padx=10, pady=3).pack(side="left", padx=(0, 8))
        if not admin:
            ttk.Button(right, text="以管理员重启", style="Small.TButton",
                       command=self._relaunch_admin).pack(side="left", padx=(0, 8))
        ttk.Button(right, text="刷新资源管理器", style="Small.TButton",
                   command=self._refresh_shell).pack(side="left")

    def _relaunch_admin(self):
        if not messagebox.askyesno(
            "以管理员重启",
            "将以管理员身份重新打开本程序（会弹出 UAC 提示），当前未保存的输入会丢失。\n\n继续吗？"
        ):
            return
        # 打包成 exe 后 sys.executable 就是自身，直接提权重启；脚本模式要带上脚本路径
        params = "" if getattr(sys, "frozen", False) else f'"{os.path.abspath(__file__)}"'
        rc = shell32.ShellExecuteW(None, "runas", sys.executable,
                                   params, None, 1)
        if int(rc) <= 32:
            messagebox.showerror("提权失败", "没能以管理员身份启动，请手动右键用管理员身份运行启动脚本。")
        else:
            self.destroy()

    def _refresh_shell(self):
        folder = self.folder_var.get().strip().strip('"')
        if folder and os.path.isdir(folder):
            notify_folder(folder)
        else:
            notify_shell()
        self.log("已通知资源管理器刷新；若外显名 / 图标 / 悬浮提示仍未更新，在文件夹窗口按 F5。", "ok")

    # ---------- 日志 / 状态栏 ----------

    def _build_log(self):
        frame = tk.Frame(self, bg=BG, padx=14, pady=6)
        frame.pack(fill="x", side="bottom")
        bar = tk.Frame(frame, bg=BG)
        bar.pack(fill="x")
        tk.Label(bar, text="操作日志", bg=BG, fg=MUTED, font=FONT_SM).pack(side="left")
        ttk.Button(bar, text="清空", command=self._clear_log,
                   style="Tiny.TButton").pack(side="right")

        # 拖动这里调整日志窗格高度（上限为窗口高度的一半）
        grip = tk.Frame(frame, bg=BG, height=8, cursor="sb_v_double_arrow")
        grip.pack(fill="x")
        tk.Frame(grip, bg=BORDER, height=1).pack(fill="x", pady=3)
        grip.bind("<ButtonPress-1>", self._start_log_drag)
        grip.bind("<B1-Motion>", self._do_log_drag)
        grip.bind("<ButtonRelease-1>", self._end_log_drag)

        self._log_height = self.settings.get("log_height") or 130
        box = tk.Frame(frame, bg="#111418", highlightbackground=BORDER,
                       highlightthickness=1, height=self._log_height)
        box.pack(fill="x")
        box.pack_propagate(False)  # 固定高度，拖动调整时才真正生效
        self._log_box = box
        self.log_text = tk.Text(box, height=4, bg="#111418", fg=FG,
                                font=FONT_MONO, wrap="word", bd=0, padx=10,
                                pady=7, insertbackground=FG, state="disabled")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self._log_scroll = scroll
        self.log_text.pack(side="left", fill="both", expand=True)
        self.after(50, self._update_log_scrollbar)  # 初始按内容决定是否显示
        self.log_text.tag_configure("ts", foreground="#5d6670")
        self.log_text.tag_configure("info", foreground="#c6cfd9")
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("hint", foreground=CYAN)

    def _start_log_drag(self, event):
        self._log_drag = (event.y_root, self._log_height)

    def _do_log_drag(self, event):
        if not getattr(self, "_log_drag", None):
            return
        start_y, start_h = self._log_drag
        dy = event.y_root - start_y
        new = start_h - dy  # 向上拖 = 增大高度
        max_h = max(80, self.winfo_height() // 2)
        new = max(60, min(max_h, new))
        self._log_height = int(new)
        self._log_box.configure(height=self._log_height)
        self._update_log_scrollbar()

    def _end_log_drag(self, _event=None):
        self._log_drag = None
        self.settings["log_height"] = self._log_height
        save_settings(self.settings)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._update_log_scrollbar()

    def log(self, message, level="info"):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] ", "ts")
        self.log_text.insert("end", message + "\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._schedule_log_refresh()

    def _schedule_log_refresh(self):
        """批量写日志时合并刷新：最多每 ~40ms 重绘一次，避免每行都强制 full redraw。"""
        if getattr(self, "_log_refresh_job", None):
            return
        self._log_refresh_job = self.after(40, self._do_log_refresh)

    def _do_log_refresh(self):
        self._log_refresh_job = None
        try:
            self.update_idletasks()
        except Exception:
            pass
        self._update_log_scrollbar()

    def _build_statusbar(self):
        self._statusbar = tk.Frame(self, bg=CARD_ALT, height=26, padx=16)
        self._statusbar.pack(fill="x", side="bottom")
        self._statusbar.pack_propagate(False)
        # 左：预留位置（传 git 后放 GitHub 仓库地址）
        tk.Label(self._statusbar, text="", bg=CARD_ALT, fg=MUTED,
                 font=FONT_SUB).pack(side="left")
        tk.Label(self._statusbar, text=f"v{APP_VERSION} · 构建于 {BUILD_DATE}",
                 bg=CARD_ALT, fg=MUTED, font=FONT_SUB).pack(side="right")

    def _run(self, action, success_level="ok"):
        try:
            result = action()
            if result:
                self.log(result, success_level)
        except Exception as exc:
            self.log(f"失败：{exc}", "err")
            messagebox.showerror("操作失败", str(exc))

    def report_callback_exception(self, exc, val, tb):
        """界面回调里的异常只写日志并提示，绝不让主循环崩掉。"""
        try:
            with open(os.path.join(app_dir(), "error.log"), "a",
                      encoding="utf-8") as handle:
                handle.write(f"\n===== {datetime.datetime.now()} · Tk 回调 =====\n"
                             + "".join(traceback.format_exception(exc, val, tb)) + "\n")
        except Exception:
            pass
        try:
            self.log(f"出错了：{val}", "err")
        except Exception:
            pass

    # ---------- Tab 1 ----------

    def _build_tab1(self, parent):
        # 右列（设置卡）略宽于左列（状态卡），标题行才放得下编码信息
        parent.columnconfigure(0, weight=5)
        parent.columnconfigure(1, weight=6)
        for row in (1, 2, 3):
            parent.rowconfigure(row, weight=1)

        # 路径行
        path_card, body = make_card(parent, "选择文件夹",
                                    "可以把文件夹直接拖进窗口")
        path_card.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(10, 8))
        body._drop_role = "folder"
        folder_entry = ttk.Entry(body, textvariable=self.folder_var, font=FONT)
        folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        folder_entry._drop_role = "folder"
        ttk.Button(body, text="选择文件夹...", command=self._pick_folder,
                   style="Accent.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(body, text="刷新状态", command=self.refresh_folder).pack(side="left")

        # 左：状态
        status_card, body = make_card(parent, "当前文件夹状态",
                                     "来自 GetFileAttributes 的真实属性")
        status_card.grid(row=1, column=0, rowspan=3, sticky="nsew", padx=(8, 4), pady=(0, 8))
        self.badge_frame = tk.Frame(body, bg=CARD)
        self.badge_frame.pack(fill="x")
        self.info_var = tk.StringVar(value="尚未选择文件夹")
        tk.Label(body, textvariable=self.info_var, bg=CARD, fg=MUTED,
                 font=FONT_SM, justify="left", wraplength=420,
                 anchor="w").pack(fill="x", pady=(12, 0))
        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=12)
        self.detail_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.detail_var, bg=CARD, fg=FG,
                 font=FONT_SM, justify="left", wraplength=420,
                 anchor="w").pack(fill="x")

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=12)
        tk.Label(body, text="属性快捷操作", bg=CARD, fg=FG,
                 font=FONT_B).pack(anchor="w")
        attr_row = tk.Frame(body, bg=CARD)
        attr_row.pack(fill="x", pady=(8, 0))
        attr_row.columnconfigure(0, weight=1)
        attr_row.columnconfigure(1, weight=1)
        for index, (text, flag, on) in enumerate([
            ("+S 设为系统", A_SYSTEM, True),
            ("-S 取消系统", A_SYSTEM, False),
            ("+R 设为只读", A_READONLY, True),
            ("-R 取消只读", A_READONLY, False),
        ]):
            self._attribute_button(attr_row, text, flag, on, index)
        ini_row = tk.Frame(body, bg=CARD)
        ini_row.pack(fill="x", pady=(2, 0))
        ttk.Button(ini_row, text="修正 desktop.ini 属性（A/H/S）",
                   command=self._fix_ini_attrs).pack(side="left")
        tk.Label(body,
                 text="Windows 自建的 desktop.ini 是 A+Hidden+System 隐藏系统文件，不会显示在文件夹里；"
                      "本工具创建 / 修改时也按这个标准保存。若用记事本手工改后掉了属性，点上面按钮修回。\n"
                      "desktop.ini 只在带 R 或 S 标志的文件夹上生效；应用时会自动补 READONLY。"
                      "取消全部设置后可在此手动去掉。",
                 bg=CARD, fg=MUTED, font=FONT_SM, wraplength=420,
                 justify="left").pack(anchor="w", pady=(10, 0))

        # 右：外显名（编码状态放在标题右侧）
        def name_head(head):
            box = tk.Frame(head, bg=CARD)
            box.pack(side="right")
            tk.Label(box, text="写入编码", bg=CARD, fg=MUTED,
                     font=FONT_SM).pack(side="left", padx=(0, 6))
            self.enc_text = tk.Label(box, text="", bg=CARD, fg=GREEN, font=FONT_SM)
            self.enc_text.pack(side="left")

        name_card, body = make_card(parent, "外显名", None, right=name_head)
        name_card.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=(0, 4))
        tk.Label(body, text="LocalizedResourceName · 资源管理器中显示的名字"
                            "（不影响真实文件夹名）",
                 bg=CARD, fg=MUTED, font=FONT_SM, anchor="w", justify="left",
                 wraplength=460).pack(anchor="w")
        ttk.Entry(body, textvariable=self.name_var, font=FONT).pack(
            fill="x", pady=(8, 8))
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x")
        ttk.Button(row, text="应用外显名", style="Accent.Small.TButton",
                   command=self._apply_name).pack(side="left")
        ttk.Button(row, text="取消外显名", style="Danger.Small.TButton",
                   command=self._clear_name).pack(side="left", padx=6)
        ttk.Button(row, text="用记事本打开 desktop.ini", style="Small.TButton",
                   command=self._open_ini).pack(side="right")

        # 右：图标设置
        icon_card, body = make_card(parent, "图标设置", "IconResource")
        icon_card.grid(row=2, column=1, sticky="nsew", padx=(4, 8), pady=(4, 4))
        icon_entry = ttk.Entry(body, textvariable=self.icon_var, font=FONT)
        icon_entry.pack(side="top", fill="x", pady=(0, 8))
        icon_entry._drop_role = "icon"
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x")
        ttk.Button(row, text="浏览图标文件...",
                   command=self._pick_icon).pack(side="left")
        tk.Label(row, text="索引", bg=CARD, fg=MUTED,
                 font=FONT_SM).pack(side="left", padx=(12, 5))
        ttk.Entry(row, textvariable=self.icon_idx_var, width=7,
                  font=FONT).pack(side="left")
        row2 = tk.Frame(body, bg=CARD)
        row2.pack(fill="x", pady=(7, 0))
        ttk.Button(row2, text="应用图标", style="Accent.TButton",
                   command=self._apply_icon).pack(side="left")
        ttk.Button(row2, text="清除图标", style="Danger.TButton",
                   command=self._clear_icon).pack(side="left", padx=6)
        tk.Label(body,
                 text="支持 .ico / .exe / .dll / .icl；索引是从 0 开始的图标序号，也可填负数（资源 ID）。",
                 bg=CARD, fg=MUTED, font=FONT_SM, wraplength=430,
                 justify="left").pack(anchor="w", pady=(10, 0))
        self.icon_hint = tk.Label(body, text="", bg=CARD, fg=CYAN,
                                  font=FONT_SM, anchor="w", justify="left")
        self.icon_hint.pack(anchor="w", pady=(4, 0))

        # 右：悬浮标注
        tip_card, body = make_card(parent, "悬浮标注", "InfoTip · 鼠标悬停提示")
        tip_card.grid(row=3, column=1, sticky="nsew", padx=(4, 8), pady=(4, 8))
        tk.Label(body, text="鼠标停在文件夹上时显示的说明，也会出现在资源管理器信息区",
                 bg=CARD, fg=MUTED, font=FONT_SM, anchor="w",
                 justify="left").pack(anchor="w")
        ttk.Entry(body, textvariable=self.tip_var, font=FONT).pack(
            fill="x", pady=(8, 8))
        tip_row = tk.Frame(body, bg=CARD)
        tip_row.pack(fill="x")
        ttk.Button(tip_row, text="应用悬浮提示", style="Accent.TButton",
                   command=self._apply_tip).pack(side="left")
        ttk.Button(tip_row, text="清除悬浮提示", style="Danger.TButton",
                   command=self._clear_tip).pack(side="left", padx=6)
        tk.Label(body,
                 text="写入 [.ShellClassInfo] 的 InfoTip= 一行：单行文本（\\n 会原样显示，不换行），"
                      "也支持 @shell32.dll,-12690 这类系统资源引用。改完按 F5 或刷新资源管理器。",
                 bg=CARD, fg=MUTED, font=FONT_SM, wraplength=430,
                 justify="left").pack(anchor="w", pady=(10, 0))

    def _pick_folder(self):
        path = filedialog.askdirectory(title="选择要设置的文件夹", mustexist=True)
        if path:
            self.folder_var.set(os.path.abspath(path))
            self.refresh_folder()

    def _pick_icon(self):
        path = filedialog.askopenfilename(
            title="选择图标文件",
            filetypes=[("图标 / 可执行文件", "*.ico *.exe *.dll *.icl"),
                       ("图片", "*.png *.gif"), ("所有文件", "*.*")]
        )
        if path:
            self.icon_var.set(path)

    def _encoding_key(self):
        return "ansi" if self.enc_var.get() == "ANSI" else "utf16"

    def _sync_encoding_label(self):
        ansi = self.enc_var.get() == "ANSI"
        if hasattr(self, "enc_text"):
            self.enc_text.configure(
                text="ANSI / 系统代码（GBK）" if ansi else "UTF-16LE",
                fg=GREEN if ansi else AMBER
            )
        for key, button in getattr(self, "enc_buttons", {}).items():
            button.configure(
                style="Accent.TButton" if key == self.enc_var.get() else "TButton"
            )

    def _set_encoding(self, key):
        self.enc_var.set(key)
        self.settings["encoding"] = key
        save_settings(self.settings)
        self._sync_encoding_label()
        self.log("desktop.ini 写入编码设为 " + key + "（已保存，下次启动保持）", "ok")

    def _sync_scale_ui(self):
        self.zoom_var.set(f'{self.settings["scale"] * 100:.0f}')

    def _set_zoom(self, value=None):
        raw = str(self.zoom_var.get() if value is None else value).strip()
        raw = raw.rstrip("%").strip()
        try:
            percent = float(raw)
        except (TypeError, ValueError):
            messagebox.showwarning("输入无效", "请输入百分比数字，例如 100、125")
            self._sync_scale_ui()
            return
        percent = min(200.0, max(60.0, percent))
        self.settings["scale"] = percent / 100.0
        save_settings(self.settings)
        apply_font_scale(self.settings["scale"])
        self._setup_style()  # 让 ttk 控件也用上新字体
        self._sync_scale_ui()
        self.after_idle(self._fit_link_table)
        self.log(f"界面文字缩放设为 {percent:.0f}%（已保存，下次启动保持）", "ok")

    def _zoom_step(self, delta):
        """上下箭头点击：按 5% 步进并立即应用。"""
        try:
            val = float(str(self.zoom_var.get()).rstrip("%").strip() or "100")
        except (TypeError, ValueError):
            val = 100.0
        val = min(200.0, max(60.0, val + delta))
        self.zoom_var.set(f"{val:.0f}")
        self._set_zoom()

    def refresh_folder(self):
        folder = self.folder_var.get().strip().strip('"')
        if not folder:
            self.log("请先选择文件夹。", "warn")
            return
        if not os.path.isdir(folder):
            self.log(f"不是有效的文件夹：{folder}", "err")
            return
        for widget in self.badge_frame.winfo_children():
            widget.destroy()
        try:
            info = read_folder_info(folder)
        except Exception as exc:
            self.log(f"读取属性失败：{exc}", "err")
            return
        attrs = info["attrs"]
        badges = [(text, bg, fg, tip) for bit, text, bg, fg, tip in ATTR_INFO
                  if attrs & bit and bit != A_NORMAL]
        if not badges:
            badges = [("NORMAL 普通", "#2b3138", "#a9b6c4", "未设置其它属性")]
        for index, (text, bg, fg, _tip) in enumerate(badges):
            tk.Label(self.badge_frame, text="  " + text + "  ", bg=bg, fg=fg,
                     font=FONT_SM, pady=3).grid(
                         row=index // 2, column=index % 2,
                         sticky="w", padx=(0, 6), pady=3)
        ini_desc = "不存在"
        if info["ini_exists"]:
            ini_desc = (f"存在（{info['ini_encoding']}）· 属性 "
                        f"{describe_attrs(info['ini_attrs'])} · 0x{info['ini_attrs']:08X}")
            if not (info["ini_attrs"] & A_HIDDEN and info["ini_attrs"] & A_SYSTEM):
                ini_desc += "  ← 非系统标准，会显示在文件夹里，建议点「修正 desktop.ini 属性」"
        self.info_var.set(
            f"真实名称：{os.path.basename(os.path.normpath(folder))}\n"
            f"完整路径：{folder}\n"
            f"文件夹属性：0x{attrs:08X}（{describe_attrs(attrs)}）\n"
            f"desktop.ini：{ini_desc}"
        )
        parts = [
            "当前外显名：" + (info["name"] or "（未设置，显示真实文件夹名）"),
            "当前悬浮提示：" + (info["tip"] or "（未设置）"),
            "当前图标：" + (info["icon"] or "（未设置）"),
        ]
        if folder_has_shell_flag(folder):
            parts.append("R / S 系统文件夹标志已开启 —— desktop.ini 可以生效")
        elif info["ini_exists"]:
            parts.append("注意：缺少 R / S 标志，desktop.ini 不生效，请点下方 [+R] 或 [+S]")
        else:
            parts.append("应用外显名 / 图标 / 悬浮提示时会自动补上 READONLY 标志")
        self.detail_var.set("\n".join(parts))
        # 只有切换文件夹时才把已保存的值载入输入框，避免覆盖用户尚未应用的新输入
        key = os.path.normcase(os.path.abspath(folder))
        if self._loaded_folder != key:
            self._loaded_folder = key
            self.name_var.set(info["name"] or "")
            self.tip_var.set(info["tip"] or "")
            if info["icon"]:
                icon_path, icon_index = split_icon_resource(info["icon"])
                self.icon_var.set(icon_path)
                self.icon_idx_var.set(str(icon_index))
            else:
                self.icon_var.set("")
                self.icon_idx_var.set("0")
        self.icon_hint.configure(
            text=("已读取现有图标：" + info["icon"]) if info["icon"] else ""
        )
        self.log(f"已读取「{folder}」的属性：{', '.join(b[0] for b in badges)}", "info")

    def _folder(self):
        folder = self.folder_var.get().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            raise ValueError("请先选择一个存在的文件夹")
        return folder

    def _apply_name(self):
        def action():
            folder = self._folder()
            result = apply_localized_name(folder, self.name_var.get(), self._encoding_key())
            self.refresh_folder()
            self.log("提示：若资源管理器没立刻变，按 F5 或点右上角「刷新资源管理器」。", "hint")
            return result
        self._run(action)

    def _clear_name(self):
        def action():
            folder = self._folder()
            result = clear_localized_name(folder)
            self.name_var.set("")
            self.refresh_folder()
            return result
        self._run(action)

    def _apply_tip(self):
        def action():
            folder = self._folder()
            result = apply_infotip(folder, self.tip_var.get(), self._encoding_key())
            self.refresh_folder()
            self.log("悬浮提示有缓存；未出现时按 F5、点「刷新资源管理器」，必要时重启资源管理器进程。", "hint")
            return result
        self._run(action)

    def _clear_tip(self):
        def action():
            folder = self._folder()
            result = clear_infotip(folder)
            self.tip_var.set("")
            self.refresh_folder()
            return result
        self._run(action)

    def _fix_ini_attrs(self):
        def action():
            folder = self._folder()
            attrs = normalize_ini_attrs(folder)
            self.refresh_folder()
            return (f"desktop.ini 属性已修正为 0x{attrs:08X}"
                    f"（{describe_attrs(attrs)}），与 Windows 自建的一致，不再显示在文件夹里。")
        self._run(action)

    def _open_ini(self):
        def action():
            result = open_ini_in_editor(self._folder())
            self.refresh_folder()
            return result
        self._run(action)

    def _apply_icon(self):
        def action():
            folder = self._folder()
            raw_idx = self.icon_idx_var.get().strip() or "0"
            if not re.fullmatch(r"-?\d+", raw_idx):
                raise ValueError("图标索引必须是整数")
            result = apply_icon(folder, self.icon_var.get(), int(raw_idx),
                                self._encoding_key())
            self.refresh_folder()
            self.log("图标可能有缓存；未更新时点「刷新资源管理器」或重启资源管理器进程。", "hint")
            return result
        self._run(action)

    def _clear_icon(self):
        def action():
            folder = self._folder()
            result = clear_icon(folder)
            self.icon_var.set("")
            self.refresh_folder()
            return result
        self._run(action)

    def _attribute_button(self, parent, text, flag, on, index):
        def action():
            folder = self._folder()
            set_attr(folder, flag, on)
            self.refresh_folder()
            return f"已{'设置' if on else '清除'} {text.split()[-1]} 属性（0x{get_attrs(folder):08X}）"
        ttk.Button(parent, text=text, command=lambda: self._run(action)).grid(
            row=index // 2, column=index % 2, sticky="ew",
            padx=(0, 6), pady=(0, 6))

    # ---------- Tab 2 ----------

    def _build_tab2(self, parent):
        parent.columnconfigure(0, weight=2, uniform="t2")
        parent.columnconfigure(1, weight=1, uniform="t2")
        parent.rowconfigure(1, weight=1)

        # ---- 创建（左 2/3） ----
        form_card, body = make_card(
            parent, "创建链接",
            "源对象必须已存在，链接位置必须不存在 · 支持拖入文件 / 文件夹")
        form_card.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=(10, 8))
        body.columnconfigure(1, weight=1)

        self.mode_var = tk.StringVar(value="")
        mode_row = tk.Frame(body, bg=CARD)
        mode_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 9))
        tk.Label(mode_row, text="当前方式", bg=CARD, fg=MUTED,
                 font=FONT_SM).pack(side="left")
        tk.Label(mode_row, textvariable=self.mode_var, bg=CARD, fg=ACCENT,
                 font=FONT_B).pack(side="left", padx=(8, 0))
        tk.Label(mode_row, text="点右侧一行即可切换，悬浮看解释",
                 bg=CARD, fg=MUTED, font=FONT_SM).pack(side="left", padx=(10, 0))

        tk.Label(body, text="源对象", bg=CARD, fg=MUTED, font=FONT_SM,
                 anchor="w").grid(row=1, column=0, sticky="w", pady=3)
        src_entry = ttk.Entry(body, textvariable=self.src_var, font=FONT)
        src_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=3)
        src_entry._drop_role = "src"

        tk.Label(body, text="链接位置", bg=CARD, fg=MUTED, font=FONT_SM,
                 anchor="w").grid(row=2, column=0, sticky="w", pady=3)
        dst_entry = ttk.Entry(body, textvariable=self.dst_var, font=FONT)
        dst_entry.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=3)
        dst_entry._drop_role = "dst"

        actions = tk.Frame(body, bg=CARD)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="创建链接", style="Accent.Small.TButton",
                   command=self._create_link).pack(side="left")
        pick_btn = ttk.Button(actions, text="选择源 ▾", style="Small.TButton",
                              command=lambda: self._pick_source(pick_btn))
        pick_btn.pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="选父目录", style="Small.TButton",
                   command=self._pick_dst_parent).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="建议路径", style="Small.TButton",
                   command=self._suggest_path).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="识别源对象", style="Small.TButton",
                   command=self._inspect_src).pack(side="left", padx=(4, 0))
        self.link_hint = tk.Label(actions, text="", bg=CARD, fg=AMBER,
                                  font=FONT_SM, anchor="w", justify="left")
        self.link_hint.pack(side="left", fill="x", expand=True, padx=(12, 0))

        # ---- 链接方式（右 1/3：点行选中，悬浮看解释） ----
        table_card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                              highlightthickness=1, padx=12, pady=11)
        table_card.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=(10, 8))
        table = tk.Frame(table_card, bg=CARD)
        table.pack(fill="both", expand=True)
        self._type_table = table
        for column, weight in enumerate(COMPARE_WEIGHTS):
            table.columnconfigure(column, weight=weight, uniform="tcol")
        table.rowconfigure(len(TABLE_TYPES) + 1, weight=1)

        self._header_cells = []
        for column, title in enumerate(COMPARE_COLS):
            cell = tk.Frame(table, bg=CARD_ALT)
            cell.grid(row=0, column=column, sticky="nsew", padx=(0, 2))
            label = tk.Label(cell, text=title, bg=CARD_ALT, fg=MUTED,
                             font=FONT_SM, anchor="w", justify="left",
                             wraplength=90)
            label.pack(fill="both", expand=True, padx=7, pady=5)
            self._header_cells.append((cell, label))

        self.type_rows = {}
        for row_index, key in enumerate(TABLE_TYPES, start=1):
            cells = []
            for column, value in enumerate(LINK_INFO[key]["row"]):
                cell = tk.Frame(table, bg=CARD, cursor="hand2")
                cell.grid(row=row_index, column=column, sticky="nsew",
                          padx=(0, 2), pady=(2, 0))
                label = tk.Label(cell, text=value, bg=CARD, fg=FG, font=FONT_SM,
                                 anchor="nw", justify="left",
                                 wraplength=90, cursor="hand2")
                label.pack(fill="both", expand=True, padx=7, pady=6)
                for widget in (cell, label):
                    widget.bind("<Button-1>",
                                lambda _e, k=key: self._pick_link_type(k))
                    widget.bind("<Enter>",
                                lambda e, k=key: self._show_kind_hover(k, e))
                    widget.bind("<Motion>",
                                lambda e, k=key: self._show_kind_hover(k, e))
                    widget.bind("<Leave>", lambda _e: self.hover.leave())
                cells.append((cell, label))
            self.type_rows[key] = cells

        table.bind("<Configure>", lambda _e: self._fit_link_table())

        # ---- 链接记录 ----
        rec_card, body = make_card(
            parent, "链接记录",
            "默认只显示本软件创建 / 改过的链接，不会自动扫盘")
        rec_card.grid(row=1, column=0, columnspan=2, sticky="nsew",
                      padx=8, pady=(0, 10))
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        tools = tk.Frame(body, bg=CARD)
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(tools, text="扫描…",
                   command=self._scan_links).pack(side="left")
        ttk.Button(tools, text="刷新状态",
                   command=self._refresh_links).pack(side="left", padx=6)
        ttk.Button(tools, text="解除链接", style="Danger.TButton",
                   command=self._unlink_selected).pack(side="left")
        ttk.Button(tools, text="移出列表",
                   command=self._forget_selected).pack(side="left", padx=6)
        ttk.Button(tools, text="添加角标", style="Accent.Small.TButton",
                   command=self._add_badge_selected).pack(side="left", padx=6)
        ttk.Button(tools, text="移除角标", style="Danger.Small.TButton",
                   command=self._remove_badge_selected).pack(side="left", padx=6)
        self.link_count_var = tk.StringVar(value="")
        tk.Label(tools, textvariable=self.link_count_var, bg=CARD, fg=MUTED,
                 font=FONT_SM).pack(side="right")

        wrap = tk.Frame(body, bg=CARD)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap._drop_role = "scan"
        self.link_tree = ttk.Treeview(
            wrap, columns=("name", "format", "kind", "status", "time"),
            show="headings", height=7, style="Link.Treeview",
            selectmode="browse")
        for key, title, width, stretch in (
            ("name", "源文件名", 250, True),
            ("format", "格式", 76, False),
            ("kind", "方式", 96, False),
            ("status", "状态", 130, False),
            ("time", "时间", 150, False),
        ):
            self.link_tree.heading(
                key, text=title, anchor="w",
                command=lambda k=key: self._sort_links(k))
            self.link_tree.column(key, width=width, anchor="w", stretch=stretch)
        scroll = ttk.Scrollbar(wrap, orient="vertical",
                               command=self.link_tree.yview)
        self.link_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.link_tree.pack(side="left", fill="both", expand=True)
        self.link_tree.bind("<<TreeviewSelect>>", self._on_link_select)
        self.link_tree.bind("<Motion>", self._on_link_hover)
        self.link_tree.bind("<Leave>", lambda _e: self.hover.leave())
        self.link_tree.bind("<Double-1>", lambda _e: self._open_link_dir())

        self._pick_link_type("symlink")
        self.after_idle(self._fit_link_table)

    def _fit_link_table(self):
        """按列的实际宽度重算换行宽度，窄栏里也不会被截断。"""
        table = getattr(self, "_type_table", None)
        if table is None or not getattr(self, "type_rows", None):
            return
        rows = [self._header_cells] + [self.type_rows[key] for key in TABLE_TYPES
                                       if key in self.type_rows]
        for cells in rows:
            for cell, label in cells:
                try:
                    width = cell.winfo_width()
                except tk.TclError:
                    return
                if width > 24:
                    label.configure(wraplength=max(50, width - 16))

    # ---------- 方式选择与悬浮解释 ----------

    def _show_kind_hover(self, key, event):
        info = LINK_INFO[key]
        body = "\n".join(f"【{title}】{text}" for title, text in info["detail"])
        self.hover.schedule(f"kind:{key}", f"{info['name']} · {info['en']}",
                            body, event.x_root, event.y_root)

    def _pick_link_type(self, key):
        self.link_kind = key
        for other, cells in self.type_rows.items():
            bg = "#1d3550" if other == key else CARD
            for cell, label in cells:
                cell.configure(bg=bg)
                label.configure(bg=bg, fg="#ffffff" if other == key else FG)
        info = LINK_INFO[key]
        self.mode_var.set(f"{info['name']}  ·  {info['en']}")
        self.link_hint.configure(text="")
        self.log(f"已选择：{info['name']}（{info['en']}）—— {info['short']}", "info")

    # ---------- 链接记录：列表 / 扫描 / 刷新 / 解除 ----------

    STATUS_ORDER = {"已失效": 0, "链接已不存在": 1, "正常": 3}  # 其余异常 = 2

    def _sort_links(self, col):
        if self._link_sort["col"] == col:
            self._link_sort["reverse"] = not self._link_sort["reverse"]
        else:
            self._link_sort = {"col": col, "reverse": False}
        self._refresh_link_table()

    def _link_sort_key(self, rec, col):
        if col == "name":
            return record_name(rec).lower()
        if col == "format":
            return record_format(rec)
        if col == "kind":
            return LINK_KIND_NAMES.get(rec.get("kind"), str(rec.get("kind", "")))
        if col == "status":
            return self.STATUS_ORDER.get(link_status(rec), 2)
        return rec.get("time") or ""  # time

    def _refresh_link_table(self):
        if not hasattr(self, "link_tree"):
            return []
        col = self._link_sort["col"]
        reverse = self._link_sort["reverse"]
        for key, title in (("name", "源文件名"), ("format", "格式"),
                           ("kind", "方式"), ("status", "状态"),
                           ("time", "时间")):
            arrow = ""
            if key == col:
                arrow = " ▼" if reverse else " ▲"
            self.link_tree.heading(key, text=title + arrow)
        # 状态列默认（未反转）已失效在最前；时间列空值永远排在最后
        if col == "time":
            empty_key = "" if reverse else "9999-99"
            rows = sorted(
                self.link_records,
                key=lambda r: (r.get("time") or empty_key,
                               record_name(r).lower()),
                reverse=reverse)
        else:
            rows = sorted(
                self.link_records,
                key=lambda r: (self._link_sort_key(r, col),
                               record_name(r).lower()),
                reverse=reverse)
        self.link_tree.delete(*self.link_tree.get_children())
        self._link_map = {}
        normal = 0
        for rec in rows:
            status = link_status(rec)
            if status == "正常":
                normal += 1
            kind = LINK_KIND_NAMES.get(rec.get("kind"), rec.get("kind", "?"))
            iid = self.link_tree.insert("", "end", values=(
                record_name(rec), record_format(rec), kind, status,
                rec.get("time", "")))
            self._link_map[iid] = rec
        self.link_count_var.set(f"共 {len(rows)} 条 · 正常 {normal} 条")
        self.after(30, lambda: self._relayout_tab(1))
        return rows

    def _selected_record(self):
        if not hasattr(self, "link_tree"):
            return None
        selection = self.link_tree.selection()
        if not selection:
            return None
        return self._link_map.get(selection[0])

    def _on_link_select(self, _event=None):
        rec = self._selected_record()
        if rec:
            self.log(f"已选中：{record_name(rec)}  →  {rec['link']}", "info")

    def _on_link_hover(self, event):
        row = self.link_tree.identify_row(event.y)
        rec = self._link_map.get(row) if row else None
        if not rec:
            self.hover.leave()
            return
        kind = LINK_KIND_NAMES.get(rec.get("kind"), rec.get("kind", "?"))
        body = "\n".join([
            f"主文件夹（源）：{rec.get('target') or '（未记录，点「刷新状态」重读）'}",
            f"链接位置：{rec['link']}",
            f"链接方式：{kind}",
            f"当前状态：{link_status(rec)}",
            f"记录来源：{rec.get('origin', '')} {rec.get('time', '')}".strip(),
        ])
        self.hover.schedule(f"link:{rec['link']}", record_name(rec), body,
                            event.x_root, event.y_root)

    def _open_link_dir(self):
        rec = self._selected_record()
        if not rec:
            return
        folder = os.path.dirname(rec["link"])
        if folder and os.path.isdir(folder):
            try:
                os.startfile(folder)
            except OSError as exc:
                self.log(f"打不开文件夹：{exc}", "warn")

    def _refresh_links(self):
        def action():
            changed = 0
            for rec in self.link_records:
                before = (rec.get("target", ""), link_status(rec))
                refresh_record_target(rec)
                if before != (rec.get("target", ""), link_status(rec)):
                    changed += 1
            save_link_records(self.link_records)
            self._refresh_link_table()
            return f"已刷新 {len(self.link_records)} 条记录，{changed} 条信息有变化"
        self._run(action)

    def _unlink_selected(self):
        rec = self._selected_record()
        if not rec:
            messagebox.showinfo("解除链接", "先在下面的列表里选中一条记录。")
            return
        kind = LINK_KIND_NAMES.get(rec.get("kind"), rec.get("kind"))
        if not messagebox.askyesno(
            "解除链接",
            f"将删除链接本身（{kind}）：\n{rec['link']}\n\n"
            f"主文件夹 / 目标内容不会被删除：\n{rec.get('target') or '（未知）'}\n\n继续吗？"
        ):
            return

        def action():
            result = remove_link_file(rec)
            self.link_records = [
                r for r in self.link_records
                if os.path.normcase(r["link"]) != os.path.normcase(rec["link"])
            ]
            save_link_records(self.link_records)
            self._refresh_link_table()
            return result
        self._run(action)

    def _forget_selected(self):
        rec = self._selected_record()
        if not rec:
            messagebox.showinfo("移出列表", "先在下面的列表里选中一条记录。")
            return
        self.link_records = [
            r for r in self.link_records
            if os.path.normcase(r["link"]) != os.path.normcase(rec["link"])
        ]
        save_link_records(self.link_records)
        self._refresh_link_table()
        self.log("已从列表移除该记录（磁盘上的链接未改动）", "info")

    # ---------- 角落图标（角标） ----------

    def _apply_badge(self, rec):
        """给选中的链接对象加角标：文件夹链接通过 desktop.ini 的 IconResource 生效。"""
        link = rec.get("link", "")
        kind = rec.get("kind", "")
        if not link or not os.path.lexists(link):
            self.log("该记录链接已不存在，无法加角标。", "warn")
            return
        if os.path.isdir(link):
            icon = _badge_icon_for(kind, self.settings.get("badge_custom"))
            enc = "utf16" if self.enc_var.get() != "ANSI" else "ansi"
            try:
                msg = apply_icon(link, icon, 0, enc)
                self.log(f"已为「{record_name(rec)}」"
                         f"（{LINK_KIND_NAMES.get(kind, kind)}）加角标：{msg}", "ok")
            except Exception as exc:
                self.log(f"加角标失败：{exc}", "err")
        else:
            self.log("Windows 不支持给单个文件单独设置图标，角标仅对文件夹链接生效"
                     "（已记录偏好，文件链接暂不加）。", "warn")

    def _remove_badge(self, rec):
        link = rec.get("link", "")
        kind = rec.get("kind", "")
        if not link or not os.path.isdir(link):
            self.log("只有文件夹链接能移除角标（文件本身不支持自定义图标）。", "warn")
            return
        try:
            msg = clear_icon(link)
            self.log(f"已移除「{record_name(rec)}」"
                     f"（{LINK_KIND_NAMES.get(kind, kind)}）角标：{msg}", "info")
        except Exception as exc:
            self.log(f"移除角标失败：{exc}", "err")

    def _add_badge_selected(self):
        rec = self._selected_record()
        if not rec:
            messagebox.showinfo("添加角标", "先在下方列表里选中一条链接记录。")
            return
        self._apply_badge(rec)

    def _remove_badge_selected(self):
        rec = self._selected_record()
        if not rec:
            messagebox.showinfo("移除角标", "先在下方列表里选中一条链接记录。")
            return
        self._remove_badge(rec)

    def _scan_links(self):
        folder = filedialog.askdirectory(
            title="选择要扫描的文件夹（向下最多 2 层）", mustexist=True)
        if folder:
            self._start_scan(folder)

    def _start_scan(self, folder):
        if self._scanning:
            self.log("正在扫描中，请等这次结束。", "warn")
            return
        self._scanning = True
        self._scan_result = None
        skip_note = f"，跳过名单 {len(self.scan_skip)} 项" if self.scan_skip else ""
        self.log(f"开始扫描：{folder}（最多向下 2 层，不进入链接目录{skip_note}）", "info")
        self.configure(cursor="watch")
        threading.Thread(target=self._scan_worker, args=(folder,),
                         daemon=True).start()
        self.after(150, self._poll_scan)

    def _scan_worker(self, folder):
        try:
            found, count = scan_links(
                folder, skip=self.scan_skip,
                include_shortcuts=self.scan_shortcuts_var.get())
            self._scan_result = ("ok", folder, found, count)
        except Exception as exc:
            self._scan_result = ("err", folder, str(exc), 0)

    def _poll_scan(self):
        if self._scan_result is None:
            self.after(150, self._poll_scan)
            return
        state, folder, payload, count = self._scan_result
        self._scan_result = None
        self._scanning = False
        self.configure(cursor="")
        if state == "err":
            self.log(f"扫描失败：{payload}", "err")
            return
        merged, added = merge_link_records(self.link_records, payload)
        self.link_records = merged
        save_link_records(self.link_records)
        self._refresh_link_table()
        self.log(f"扫描完成：检查 {count} 个条目，发现 {len(payload)} 个链接，"
                 f"新增 {added} 条记录", "ok" if payload else "info")

    # ---------- 拖放 ----------

    def _drop_role_of(self, widget):
        while widget is not None:
            role = getattr(widget, "_drop_role", None)
            if role:
                return role
            widget = getattr(widget, "master", None)
        return None

    def _on_drop(self, paths, x, y):
        """x / y 是屏幕坐标（拖放消息里已换算好）。"""
        try:
            self._handle_drop(paths, x, y)
        except Exception as exc:
            log_exception("拖放处理")
            try:
                self.log(f"拖放处理失败：{exc}", "err")
            except Exception:
                pass

    def _handle_drop(self, paths, x, y):
        if not paths:
            return
        widget = None
        try:
            widget = self.winfo_containing(x, y)
        except Exception:
            pass
        role = self._drop_role_of(widget)
        if role and role[0] == "badge":
            self._drop_badge_icon(role[1], paths)
            return
        if role is None:
            role = ("folder", "src", "scan")[min(self._current_tab, 2)]
        if len(paths) > 1:
            self.log(f"拖入了 {len(paths)} 项，只使用第一个：{paths[0]}", "warn")
        path = paths[0]
        if role == "folder":
            if os.path.isdir(path):
                self.folder_var.set(os.path.abspath(path))
                self.refresh_folder()
            else:
                self.log("这里是「选择文件夹」，请拖入文件夹而不是文件。", "warn")
        elif role == "src":
            self.src_var.set(os.path.abspath(path))
            self._suggest_path()
            self.link_hint.configure(text="已载入源对象：" + os.path.basename(path))
            self.log(f"源对象已设为：{path}", "ok")
        elif role == "dst":
            self._set_dst_from_drop(path)
        elif role == "icon":
            if os.path.isdir(path):
                self.log("图标要拖入 .ico / .exe / .dll 文件，而不是文件夹。", "warn")
            else:
                self.icon_var.set(os.path.abspath(path))
                self.log(f"图标文件已设为：{path}", "ok")
        elif role == "scan":
            if os.path.isdir(path):
                self._start_scan(os.path.abspath(path))
            else:
                self.log("要扫描需要拖入文件夹。", "warn")

    def _drop_badge_icon(self, kind, paths):
        """把拖入的 .ico 文件设为对应链接方式的自定义角标图标。"""
        if not paths:
            return
        if len(paths) > 1:
            self.log("拖入多个文件，仅使用第一个作为角标图标。", "warn")
        icon_path = os.path.abspath(paths[0])
        if not icon_path.lower().endswith(".ico") or not os.path.isfile(icon_path):
            self.log("角标图标请拖入 .ico 文件。", "warn")
            return
        self.settings["badge_custom"][kind] = icon_path
        save_settings(self.settings)
        self._refresh_badge_custom_labels()
        self._refresh_badge_previews()
        self.log(f"已通过拖放为{LINK_KIND_NAMES[kind]}设置自定义角标图标：{icon_path}", "ok")

    def _set_dst_from_drop(self, path):
        if os.path.isdir(path):
            src = self.src_var.get().strip()
            name = os.path.basename(os.path.normpath(src)) if src else "新建链接"
            suffix = {
                "symlink": " - 符号链接", "junction": " - 目录联接",
                "hardlink": " - 硬链接", "shortcut": " - 快捷方式.lnk",
            }[self.link_kind]
            self.dst_var.set(os.path.join(path, name + suffix))
        else:
            self.dst_var.set(path)
        self.log(f"链接位置已设为：{self.dst_var.get()}", "ok")

    def _pick_source(self, anchor=None):
        """文件 / 文件夹统一入口：一个按钮弹出两项，按源对象类型自动选。"""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="选择文件…", command=self._pick_src_file)
        menu.add_command(label="选择文件夹…", command=self._pick_src_dir)
        try:
            if anchor is not None and anchor.winfo_exists():
                x = anchor.winfo_rootx()
                y = anchor.winfo_rooty() + anchor.winfo_height()
            else:
                x, y = self.winfo_pointerx(), self.winfo_pointery()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _pick_src_file(self):
        path = filedialog.askopenfilename(title="选择源文件（作为链接的目标）")
        if path:
            self.src_var.set(os.path.abspath(path))
            self._suggest_path()

    def _pick_src_dir(self):
        path = filedialog.askdirectory(title="选择源文件夹（作为链接的目标）", mustexist=True)
        if path:
            self.src_var.set(os.path.abspath(path))
            self._suggest_path()

    def _pick_dst_parent(self):
        path = filedialog.askdirectory(title="选择链接要放在哪个文件夹里", mustexist=True)
        if path:
            current = self.dst_var.get().strip()
            name = os.path.basename(current) if current else ""
            self.dst_var.set(os.path.join(os.path.abspath(path), name))

    def _suggest_path(self):
        src = self.src_var.get().strip().strip('"')
        if not src:
            self.log("先选源对象，再生成建议路径。", "warn")
            return
        base = os.path.basename(os.path.normpath(src))
        suffix = {
            "symlink": " - 符号链接", "junction": " - 目录联接",
            "hardlink": " - 硬链接", "shortcut": " - 快捷方式.lnk",
        }[self.link_kind]
        root, ext = os.path.splitext(base)
        if self.link_kind == "hardlink":
            candidate = os.path.join(os.path.dirname(src), root + suffix + ext)
        elif self.link_kind == "shortcut":
            candidate = os.path.join(os.path.dirname(src), root + suffix)
        else:
            candidate = os.path.join(os.path.dirname(src), base + suffix)
        n, final = 1, candidate
        while os.path.exists(final) or os.path.islink(final):
            n += 1
            stem, tail = os.path.splitext(candidate)
            final = f"{stem} ({n}){tail}"
        self.dst_var.set(final)

    def _inspect_src(self):
        src = self.src_var.get().strip().strip('"')
        if not src:
            self.log("先填源对象路径。", "warn")
            return
        self._run(lambda: "识别结果：" + inspect_path(src), "hint")

    def _create_link(self):
        def action():
            src = self.src_var.get().strip()
            dst = self.dst_var.get().strip()
            if not src or not dst:
                raise ValueError("源对象和链接位置都要填写")
            if self.link_kind == "symlink" and not is_admin():
                self.log("当前不是管理员：若系统未开启开发者模式，创建会失败。", "warn")
            link, desc = create_link(self.link_kind, src, dst)
            self.dst_var.set(link)
            new_rec = new_link_record(
                self.link_kind, os.path.abspath(src.strip().strip('"')), link)
            merged, _ = merge_link_records(self.link_records, [new_rec])
            self.link_records = merged
            save_link_records(self.link_records)
            self._refresh_link_table()
            self.log(f"已创建：{link}", "ok")
            if self.settings.get("badge_default") == "add":
                self._apply_badge(new_rec)
            self.log(f"    {desc}", "hint")
            return "链接创建完成，已加入下方「链接记录」。改动会立刻在另一边体现。"
        self._run(action)

    # ---------- Tab 3：设置 ----------

    def _build_tab3(self, parent):
        self._tab3_page = parent
        parent.columnconfigure(0, weight=2, uniform="t3")
        parent.columnconfigure(1, weight=1, uniform="t3")
        parent.rowconfigure(1, weight=1)
        # 设置页整体尺寸变化时，刷新跳过名单滚动条的显隐
        parent.bind("<Configure>",
                    lambda _e: self.after(60, self._update_skip_scrollbar))

        enc_card, body = make_card(parent, "desktop.ini 写入编码",
                                   "默认 ANSI，与系统一致")
        enc_card.grid(row=0, column=0, sticky="nsew", padx=(8, 4),
                      pady=(10, 8))
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x")
        self.enc_buttons = {}
        for key, text in (("ANSI", "ANSI / 系统代码页（默认）"),
                          ("UTF-16LE", "UTF-16LE（备用）")):
            button = ttk.Button(row, text=text,
                                command=lambda k=key: self._set_encoding(k))
            button.pack(side="left", padx=(0, 8))
            self.enc_buttons[key] = button
        tk.Label(body,
                 text="ANSI：和在资源管理器地址栏输入路径、用记事本直接编辑 desktop.ini 的做法完全一致，"
                      "中文系统为 GBK，兼容性最好。\n"
                      "UTF-16LE：带 BOM 的 Unicode，只在外显名 / 悬浮提示含有系统代码页无法表示的字符时才需要。",
                 bg=CARD, fg=FG, font=FONT_SM, wraplength=470,
                 justify="left").pack(anchor="w", pady=(12, 0))

        # 右 1/3：文字缩放（原样输入框 + 右侧紧贴的上下箭头，每次 ±5%）
        scale_card, body = make_card(parent, "文字缩放")
        scale_card.grid(row=0, column=1, sticky="nsew", padx=(4, 8),
                        pady=(10, 8))
        srow = tk.Frame(body, bg=CARD)
        srow.pack(fill="x")
        zoom_entry = ttk.Entry(srow, textvariable=self.zoom_var, width=8,
                               font=FONT, justify="right")
        zoom_entry.pack(side="left")
        zoom_entry.bind("<Return>", lambda _e: self._set_zoom())
        tk.Label(srow, text="%", bg=CARD, fg=FG,
                 font=FONT).pack(side="left", padx=(4, 0))
        # 紧贴输入框右侧、同风格的上下箭头（每次 ±5% 并即时应用）
        ttk.Button(srow, text="\u25b2", width=2, style="Tiny.TButton",
                   command=lambda: self._zoom_step(+5)).pack(side="left", padx=(3, 0))
        ttk.Button(srow, text="\u25bc", width=2, style="Tiny.TButton",
                   command=lambda: self._zoom_step(-5)).pack(side="left")
        # 应用：正常宽度、未选中样式
        ttk.Button(body, text="应用", command=self._set_zoom).pack(
            anchor="w", pady=(8, 0))
        tk.Label(body, text="上下箭头每次调整 5%，回车或点应用生效。",
                 bg=CARD, fg=MUTED, font=FONT_SM, justify="left").pack(
            anchor="w", pady=(6, 4))

        # 左 2/3：扫描跳过名单
        skip_card, body = make_card(
            parent, "扫描跳过名单",
            "名单内的文件夹扫描时不记录、不深入")
        skip_card.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=(0, 10))
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        skip_tools = tk.Frame(body, bg=CARD)
        skip_tools.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(skip_tools, text="添加文件夹...",
                   command=self._skip_add).pack(side="left")
        ttk.Button(skip_tools, text="移除选中", style="Danger.TButton",
                   command=self._skip_remove).pack(side="left", padx=6)
        self.skip_count_var = tk.StringVar(value="")
        tk.Label(skip_tools, textvariable=self.skip_count_var, bg=CARD,
                 fg=MUTED, font=FONT_SM).pack(side="right")
        skip_wrap = tk.Frame(body, bg=CARD)
        skip_wrap.grid(row=1, column=0, sticky="nsew")
        self.skip_tree = ttk.Treeview(
            skip_wrap, columns=("name", "path"), show="headings", height=6,
            style="Link.Treeview", selectmode="browse")
        self.skip_tree.heading("name", text="文件夹名")
        self.skip_tree.heading("path", text="路径")
        self.skip_tree.column("name", width=150, anchor="w")
        self.skip_tree.column("path", width=320, anchor="w", stretch=True)
        self.skip_scroll = ttk.Scrollbar(skip_wrap, orient="vertical",
                                         command=self.skip_tree.yview)
        self.skip_tree.configure(yscrollcommand=self.skip_scroll.set)
        self.skip_scroll.pack(side="right", fill="y")
        self.skip_tree.pack(side="left", fill="both", expand=True)
        self._refresh_skip_list()

        # 右 1/3：链接扫描范围
        range_card, body = make_card(parent, "链接扫描",
                                     "「扫描」按钮的查找范围")
        range_card.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=(0, 10))
        self.scan_range_buttons = {}
        for value, text in ((False, "不含快捷方式"),
                            (True, "包含快捷方式")):
            button = ttk.Button(
                body, text=text,
                command=lambda v=value: self._set_scan_shortcuts(v))
            button.pack(fill="x", pady=(0, 6))
            self.scan_range_buttons[value] = button
        tk.Label(body,
                 text="不含快捷方式（默认）：只扫描符号链接、目录联接、硬链接三种真链接。\n\n"
                      "包含快捷方式：额外把 .lnk 快捷方式也扫进链接记录。",
                 bg=CARD, fg=MUTED, font=FONT_SM, wraplength=240,
                 justify="left").pack(anchor="w", pady=(8, 0))
        self._sync_scan_range_buttons()

        # 全宽：链接角落图标
        badge_card, body = make_card(
            parent, "链接角落图标",
            "给链接对象加角标，仅对链接记录里的文件夹链接生效")
        badge_card.grid(row=2, column=0, columnspan=2, sticky="ew",
                       padx=8, pady=(0, 10))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=5)

        # 左：默认行为（三项，上下均匀分布以填满卡片高度）
        col_a = tk.Frame(body, bg=CARD)
        col_a.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(col_a, text="默认行为", bg=CARD, fg=FG,
                 font=FONT_B).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.badge_default_buttons = {}
        for idx, (key, text) in enumerate(
                (("add", "添加图标"), ("none", "不加图标"),
                 ("restore", "恢复自带图标"))):
            col_a.rowconfigure(idx + 1, weight=1)
            b = ttk.Button(col_a, text=text,
                           command=lambda k=key: self._set_badge_default(k))
            b.grid(row=idx + 1, column=0, sticky="ew", padx=2)
            self.badge_default_buttons[key] = b

        # 右：三种链接方式的角标预览 + 各自自定义按钮（按钮放在图标下方）
        col_b = tk.Frame(body, bg=CARD)
        col_b.grid(row=0, column=1, sticky="nsew")
        col_b.columnconfigure((0, 1, 2), weight=1)
        self._badge_previews = {}
        self.badge_custom_labels = {}
        for col, kind in enumerate(("symlink", "junction", "hardlink")):
            sub = tk.Frame(col_b, bg=CARD)
            sub.grid(row=0, column=col, sticky="nsew", padx=6)
            sub.rowconfigure(0, weight=1)
            lbl = tk.Label(sub, bg=CARD, anchor="center")
            lbl.pack(fill="both", expand=True)
            lbl._drop_role = ("badge", kind)
            b = ttk.Button(sub, text=f"{LINK_KIND_NAMES[kind]}自定义",
                           command=lambda k=kind: self._pick_badge_custom(k))
            b.pack(fill="x", pady=(8, 0))
            b._drop_role = ("badge", kind)
            self._badge_previews[kind] = lbl
            self.badge_custom_labels[kind] = b

        self._sync_badge_default()
        self._refresh_badge_custom_labels()
        self._refresh_badge_previews()

    def _refresh_skip_list(self):
        if not hasattr(self, "skip_tree"):
            return
        self.skip_tree.delete(*self.skip_tree.get_children())
        for path in self.scan_skip:
            name = os.path.basename(os.path.normpath(path)) or path
            self.skip_tree.insert("", "end", values=(name, path))
        count = len(self.scan_skip)
        self.skip_count_var.set(f"共 {count} 项" if count else "名单为空")
        self.after(50, self._update_skip_scrollbar)

    def _auto_hide_scrollbar(self, scroll, widget, tries=0):
        """内容未溢出时隐藏滚动条、溢出才显示，消除空白长条。skip / 日志共用。"""
        if scroll is None or widget is None or not widget.winfo_exists():
            return
        # 控件尚未布局时延迟重试（最多约 3 秒）
        if not widget.winfo_ismapped():
            if tries < 40:
                self.after(80, lambda: self._auto_hide_scrollbar(scroll, widget, tries + 1))
            return
        try:
            first, last = widget.yview()
        except Exception:
            return
        need = (first > 0.0001 or last < 0.9999)
        if need:
            if not scroll.winfo_ismapped():
                scroll.pack(side="right", fill="y")
        else:
            if scroll.winfo_ismapped():
                scroll.pack_forget()

    def _update_skip_scrollbar(self, tries=0):
        self._auto_hide_scrollbar(getattr(self, "skip_scroll", None),
                                  getattr(self, "skip_tree", None), tries)

    def _update_log_scrollbar(self, tries=0):
        self._auto_hide_scrollbar(getattr(self, "_log_scroll", None),
                                  getattr(self, "log_text", None), tries)

    def _skip_add(self):
        folder = filedialog.askdirectory(
            title="选择要跳过扫描的文件夹", mustexist=True)
        if not folder:
            return
        folder = os.path.abspath(folder)
        if any(os.path.normcase(p) == os.path.normcase(folder)
               for p in self.scan_skip):
            self.log("该文件夹已在跳过名单里。", "warn")
            return
        self.scan_skip.append(folder)
        self.settings["scan_skip"] = list(self.scan_skip)
        save_settings(self.settings)
        self._refresh_skip_list()
        self.log(f"已加入跳过名单（扫描时将跳过）：{folder}", "ok")

    def _skip_remove(self):
        if not hasattr(self, "skip_tree"):
            return
        selection = self.skip_tree.selection()
        if not selection:
            messagebox.showinfo("移除跳过名单", "先在名单里选中一项。")
            return
        values = self.skip_tree.item(selection[0], "values")
        path = values[1] if len(values) > 1 else ""
        self.scan_skip = [
            p for p in self.scan_skip
            if os.path.normcase(p) != os.path.normcase(path)]
        self.settings["scan_skip"] = list(self.scan_skip)
        save_settings(self.settings)
        self._refresh_skip_list()
        self.log(f"已从跳过名单移除：{path}", "info")

    def _set_scan_shortcuts(self, value):
        self.scan_shortcuts_var.set(bool(value))
        self.settings["scan_shortcuts"] = bool(value)
        save_settings(self.settings)
        self._sync_scan_range_buttons()
        self.log("链接扫描范围：" + ("包含快捷方式" if value else "不含快捷方式"),
                 "info")

    def _sync_scan_range_buttons(self):
        if not hasattr(self, "scan_range_buttons"):
            return
        current = bool(self.scan_shortcuts_var.get())
        for value, button in self.scan_range_buttons.items():
            button.configure(
                style="Accent.TButton" if value == current else "TButton")

    # ---------- 链接角落图标设置 ----------

    def _set_badge_default(self, key):
        self.settings["badge_default"] = key
        save_settings(self.settings)
        self._sync_badge_default()

    def _sync_badge_default(self):
        if not hasattr(self, "badge_default_buttons"):
            return
        current = self.settings.get("badge_default", "add")
        for key, button in self.badge_default_buttons.items():
            button.configure(
                style="Accent.TButton" if key == current else "TButton")

    def _pick_badge_custom(self, kind):
        path = filedialog.askopenfilename(
            title=f"选择{LINK_KIND_NAMES[kind]}的角标图标（.ico）",
            filetypes=[("图标文件", "*.ico"), ("所有文件", "*.*")])
        if not path:
            return
        path = os.path.abspath(path)
        if not path.lower().endswith(".ico"):
            self.log("角标图标请使用 .ico 文件。", "warn")
            return
        self.settings["badge_custom"][kind] = path
        save_settings(self.settings)
        self._refresh_badge_custom_labels()
        self._refresh_badge_previews()
        self.log(f"已为{LINK_KIND_NAMES[kind]}设置自定义角标图标：{path}", "ok")

    def _refresh_badge_custom_labels(self):
        for kind, button in getattr(self, "badge_custom_labels", {}).items():
            custom = self.settings["badge_custom"].get(kind, "")
            text = (f"{LINK_KIND_NAMES[kind]}自定义\n{custom}"
                    if custom else f"{LINK_KIND_NAMES[kind]}自定义")
            button.configure(text=text)

    def _refresh_badge_previews(self):
        previews = getattr(self, "_badge_previews", None)
        if not previews:
            return
        for kind in ("symlink", "junction", "hardlink"):
            path = _badge_icon_for(kind, self.settings.get("badge_custom"))
            photo = None
            if HAS_PIL and path and os.path.exists(path):
                try:
                    im = Image.open(path).convert("RGBA").resize(
                        (96, 96), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(im)
                except Exception:
                    photo = None
            lbl = previews.get(kind)
            if lbl is not None:
                lbl.configure(image=photo)
                lbl.image = photo



# ---------------------------------------------------------------------------

def main():
    # 打包成 windowed exe 后 sys.stdout / sys.stderr 是 None，
    # 任何 print / 异常回溯都会炸，先兜住。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    # 硬崩溃（访问冲突等）也留一份现场，方便事后定位
    try:
        import faulthandler
        crash = open(os.path.join(app_dir(), "error.log"), "a",
                     buffering=1, encoding="utf-8", errors="replace")
        faulthandler.enable(file=crash, all_threads=True)
    except Exception:
        pass
    app = FolderStudio()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        try:
            log_path = os.path.join(app_dir(), "error.log")
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"\n===== {datetime.datetime.now()} =====\n{detail}\n")
        except Exception:
            pass
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "程序启动失败，详情已写入 error.log：\n\n" + detail,
                "Folder Studio", 0x10
            )
        except Exception:
            pass
