"""URL 与原样文本二维码的 ttk 批量生成界面。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import TypedDict
from urllib.parse import unquote, urlsplit

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageTk


BROWSER_PATHS = {
    "Chrome": (Path("Google") / "Chrome" / "Application" / "chrome.exe", "chrome.exe"),
    "Firefox": (Path("Mozilla Firefox") / "firefox.exe", "firefox.exe"),
    "Edge": (Path("Microsoft") / "Edge" / "Application" / "msedge.exe", "msedge.exe"),
}


class Profile(TypedDict):
    """描述可保存的命名配置组。"""

    name: str
    mode: str
    prefix: list[str]


def _configuration_path() -> Path:
    """返回与 GUI 脚本同目录的独立配置文件路径。"""
    return Path(__file__).with_name("qr_gui_config.json")


def _center_window(window: tk.Tk | tk.Toplevel, width: int, height: int) -> None:
    """设置窗口尺寸，并将窗口放置到屏幕中央。"""
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def _find_browser_executable(browser_name: str) -> Path | None:
    """按 PATH 和常见 Windows 安装目录查找所选浏览器。"""
    relative_path, executable_name = BROWSER_PATHS[browser_name]
    executable = shutil.which(executable_name)
    if executable:
        return Path(executable)

    install_roots = [
        Path(value)
        for value in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        )
        if value
    ]
    for root in install_roots:
        candidate = root / relative_path
        if candidate.is_file():
            return candidate
    return None


def _read_prefixes(value: object, config_path: Path) -> list[str]:
    """兼容读取旧版单前缀和新版前缀列表。"""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(isinstance(prefix, str) for prefix in value):
        return value
    raise ValueError(f"配置文件中的固定前缀必须是字符串或字符串列表：{config_path}")


def _load_configuration() -> dict[str, object]:
    """读取并校验配置组和最近使用设置，不读取多行输入正文。"""
    config_path = _configuration_path()
    if not config_path.exists():
        return {
            "profiles": [],
            "last_mode": "raw",
            "last_prefix": [],
            "last_profile": "",
            "last_browser": "Edge",
        }

    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式无效：{config_path}")

    profiles = data.get("profiles", [])
    if not isinstance(profiles, list):
        raise ValueError(f"配置文件中的 profiles 必须是列表：{config_path}")

    validated_profiles: list[Profile] = []
    # 配置名称用作选择键，重复名称会让启动时无法确定应恢复哪一组。
    names = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError(f"配置文件包含无效的配置项：{config_path}")
        name = profile.get("name")
        mode = profile.get("mode")
        prefixes = _read_prefixes(profile.get("prefix", profile.get("prefixes", [])), config_path)
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"配置文件包含空名称或重复名称：{config_path}")
        if not isinstance(mode, str) or mode not in {"prefix", "raw"}:
            raise ValueError(f"配置文件包含无效的模式：{config_path}")
        names.add(name)
        validated_profiles.append(Profile(name=name, mode=mode, prefix=prefixes))

    last_mode = data.get("last_mode", "raw")
    last_prefix = _read_prefixes(data.get("last_prefix", data.get("last_prefixes", [])), config_path)
    last_profile = data.get("last_profile", "")
    last_browser = data.get("last_browser", "Edge")
    if not isinstance(last_mode, str) or last_mode not in {"prefix", "raw"}:
        raise ValueError(f"配置文件中的上次使用模式无效：{config_path}")
    if not isinstance(last_profile, str):
        raise ValueError(f"配置文件中的上次配置名称无效：{config_path}")
    if not isinstance(last_browser, str) or last_browser not in BROWSER_PATHS:
        raise ValueError(f"配置文件中的上次使用浏览器无效：{config_path}")
    return {
        "profiles": validated_profiles,
        "last_mode": last_mode,
        "last_prefix": last_prefix,
        "last_profile": last_profile,
        "last_browser": last_browser,
    }


def _write_configuration(configuration: dict[str, object]) -> None:
    """将配置组及最近使用的模式、前缀和浏览器写入独立 JSON 文件。"""
    config_path = _configuration_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(configuration, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _wrap_cell_text(text: str, width: int, font: tkfont.Font) -> str:
    """根据表格字体的实际像素宽度为单元格文字换行。"""
    wrapped_lines = []
    for paragraph in text.split("\n"):
        current_line = ""
        for character in paragraph:
            if current_line and font.measure(current_line + character) > width:
                wrapped_lines.append(current_line)
                current_line = character
            else:
                current_line += character
        wrapped_lines.append(current_line)
    return "\n".join(wrapped_lines)


def _sanitize_filename(name: str) -> str:
    """将输入内容转换为安全的文件名主体。"""
    name = unquote(name).replace("/", "_").replace("\\", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = re.sub(r"_+", "_", name).strip(" _.")
    return name[:200] or "unnamed"


def _build_output_path(save_dir: Path, text: str) -> Path:
    """按输入路径生成带时间戳且不会覆盖现有文件的 PNG 路径。"""
    parsed = urlsplit(text)
    name = parsed.path or ""
    if parsed.query:
        name += "?" + parsed.query
    if parsed.fragment:
        name += "#" + parsed.fragment
    name = re.sub(r"^[/:?#]+", "", name)
    name = _sanitize_filename(name or "root")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = save_dir / f"{timestamp}_{name}.png"
    counter = 2
    while output_path.exists():
        output_path = save_dir / f"{timestamp}_{name}_{counter:02d}.png"
        counter += 1
    return output_path


def _extract_url_display_text(text: str) -> str:
    """提取二维码图片顶部显示的 URL 路径、查询参数和片段。"""
    parsed = urlsplit(text)
    display_text = parsed.path or "/"
    if parsed.query:
        display_text += "?" + parsed.query
    if parsed.fragment:
        display_text += "#" + parsed.fragment
    return display_text


def _load_url_font(size: int) -> ImageFont.ImageFont:
    """优先加载系统中文字体，用于在二维码图片上绘制 URL 路径。"""
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _measure_url_text(text: str, font: ImageFont.ImageFont) -> int:
    """兼容不同 Pillow 版本测量 URL 文本宽度。"""
    if hasattr(font, "getlength"):
        return int(font.getlength(text))
    left, _, right, _ = font.getbbox(text)
    return right - left


def _wrap_url_display_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    """按字体实际像素宽度换行二维码图片顶部的 URL 文本。"""
    lines = []
    current_line = ""
    for character in text:
        candidate = current_line + character
        if current_line and _measure_url_text(candidate, font) > max_width:
            lines.append(current_line)
            current_line = character
        else:
            current_line = candidate
    if current_line:
        lines.append(current_line)
    return lines or [""]


def _create_qr_image(text: str, save_dir: Path, show_url_text: bool) -> Path:
    """在当前文件中生成二维码 PNG；URL 模式额外绘制路径说明。"""
    save_dir.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(box_size=12, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    if show_url_text:
        padding = 8
        font = _load_url_font(max(12, qr_image.width // 25))
        max_text_width = max(qr_image.width - padding * 2, min(1200, int(qr_image.width * 1.6)))
        display_text = _extract_url_display_text(text)
        lines = _wrap_url_display_text(display_text, font, max_text_width)
        font_bounds = font.getbbox("Ay")
        line_height = font_bounds[3] - font_bounds[1]
        text_width = max(_measure_url_text(line, font) for line in lines)
        text_block_height = line_height * len(lines) + padding * 2
        output_image = Image.new(
            "RGB",
            (max(qr_image.width, text_width + padding * 2), text_block_height + qr_image.height),
            "white",
        )
        draw = ImageDraw.Draw(output_image)
        for index, line in enumerate(lines):
            x = (output_image.width - _measure_url_text(line, font)) // 2
            draw.text((x, padding + index * line_height), line, font=font, fill="black")
        qr_x = (output_image.width - qr_image.width) // 2
        output_image.paste(qr_image, (qr_x, text_block_height))
    else:
        output_image = qr_image

    output_path = _build_output_path(save_dir, text)
    output_image.save(output_path, "PNG")
    return output_path


class QrGeneratorApp:
    """提供批量输入、前缀配置和二维码结果展示界面。"""

    def __init__(
        self,
        root: tk.Tk,
        title: str,
        default_save_dir: Path,
        configuration: dict[str, object],
    ) -> None:
        """创建窗口并恢复已保存的模式、前缀、浏览器和配置组。"""
        self.root = root
        self.configuration = configuration
        self.profiles: list[Profile] = list(self.configuration["profiles"])
        self.job_lines: list[str] = []
        self.job_prefixes: list[str] = []
        self.job_index = 0
        self.qr_content_count = 0
        self.job_mode = "raw"
        self.job_save_dir = default_save_dir
        self.qr_cache: dict[tuple[str, str, str], Path] = {}
        self.qr_images: list[ImageTk.PhotoImage] = []
        self.result_details: dict[str, dict[str, str]] = {}
        self.preview_window: tk.Toplevel | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_values: list[ttk.Label] = []
        self.pending_config_save: str | None = None

        last_profile = str(self.configuration["last_profile"])
        mode = str(self.configuration["last_mode"])
        prefixes = list(self.configuration["last_prefix"])
        saved_profile = next(
            (item for item in self.profiles if item["name"] == last_profile),
            None,
        )
        # 最近设置可能已手动修改；只有与配置组一致时才标记为已选中。
        selected_profile = (
            last_profile
            if saved_profile and saved_profile["mode"] == mode and saved_profile["prefix"] == prefixes
            else ""
        )
        profile_name = last_profile if saved_profile else ""

        self.root.title(title)
        self.root.minsize(760, 620)
        window_width = min(940, max(760, self.root.winfo_screenwidth() - 80))
        window_height = min(760, max(620, self.root.winfo_screenheight() - 100))
        _center_window(self.root, window_width, window_height)
        self.mode_var = tk.StringVar(value=mode)
        self.prefixes = prefixes
        self.profile_name_var = tk.StringVar(value=profile_name)
        self.profile_var = tk.StringVar(value=selected_profile)
        self.save_dir_var = tk.StringVar(value=str(default_save_dir))
        self.browser_var = tk.StringVar(value=str(self.configuration["last_browser"]))
        self._build_ui()
        self.mode_var.trace_add("write", self._schedule_config_save)
        self.prefix_text.bind("<<Modified>>", self._on_prefix_text_modified)
        self.browser_var.trace_add("write", self._schedule_config_save)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        """创建可伸缩界面；多行输入使用按 ttk 主题配色的 Text 控件。"""
        style = ttk.Style(self.root)
        background = style.lookup("TFrame", "background") or self.root.cget("background")
        field_background = style.lookup("TEntry", "fieldbackground") or "white"
        foreground = style.lookup("TEntry", "foreground") or style.lookup("TLabel", "foreground") or "black"
        font = style.lookup("TEntry", "font") or "TkDefaultFont"

        container = ttk.Frame(self.root, padding=(8, 4))
        container.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        # 输入区缩至原来约3/5，并保留最小高度，避免被结果区挤没。
        container.rowconfigure(0, weight=0, minsize=120)
        container.rowconfigure(3, weight=0, minsize=90)
        container.rowconfigure(4, weight=2)

        input_frame = ttk.LabelFrame(
            container, text="批量输入（每行生成一个二维码，空行忽略）", padding=6,
        )
        input_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        input_frame.columnconfigure(0, weight=1)
        input_frame.rowconfigure(0, weight=1)
        # ttk 没有多行文本控件；这里只用按当前主题配色的 tk.Text。
        self.input_text = tk.Text(
            input_frame,
            wrap="none",
            height=5,
            font=font,
            background=field_background,
            foreground=foreground,
            insertbackground=foreground,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=background,
            highlightcolor=style.lookup("TEntry", "focuscolor") or "#4a6984",
        )
        self.input_text.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar = ttk.Scrollbar(
            input_frame, orient="vertical", command=self.input_text.yview,
        )
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(
            input_frame, orient="horizontal", command=self.input_text.xview,
        )
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.input_text.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )

        config_frame = ttk.LabelFrame(container, text="内容配置", padding=6)
        config_frame.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        config_frame.columnconfigure(1, weight=1)
        ttk.Radiobutton(
            config_frame,
            text="固定前缀 + 每行内容",
            variable=self.mode_var,
            value="prefix",
        ).grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Radiobutton(
            config_frame,
            text="每行原样写入",
            variable=self.mode_var,
            value="raw",
        ).grid(row=0, column=1, sticky="w")

        ttk.Label(config_frame, text="配置组").grid(
            row=1, column=0, sticky="w", pady=(8, 0), padx=(0, 8),
        )
        self.profile_choice = ttk.Combobox(
            config_frame,
            textvariable=self.profile_var,
            values=[item["name"] for item in self.profiles],
            state="readonly",
        )
        self.profile_choice.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        self.profile_choice.bind("<<ComboboxSelected>>", self._select_profile)
        ttk.Label(config_frame, text="配置名称").grid(
            row=1, column=2, sticky="w", padx=(12, 8), pady=(8, 0),
        )
        ttk.Entry(config_frame, textvariable=self.profile_name_var).grid(
            row=1, column=3, sticky="ew", pady=(8, 0),
        )
        config_frame.columnconfigure(3, weight=1)
        ttk.Button(config_frame, text="保存/更新", command=self._save_profile).grid(
            row=1, column=4, padx=(8, 0), pady=(8, 0),
        )
        ttk.Button(config_frame, text="删除配置", command=self._delete_profile).grid(
            row=1, column=5, padx=(8, 0), pady=(8, 0),
        )

        ttk.Label(config_frame, text="前缀").grid(
            row=2, column=0, sticky="w", pady=(8, 0), padx=(0, 8),
        )
        prefix_frame = ttk.Frame(config_frame)
        prefix_frame.grid(row=2, column=1, columnspan=5, sticky="ew", pady=(8, 0))
        prefix_frame.columnconfigure(0, weight=1)
        prefix_frame.rowconfigure(0, weight=1)
        self.prefix_text = tk.Text(
            prefix_frame,
            wrap="none",
            height=3,
            font=font,
            background=field_background,
            foreground=foreground,
            insertbackground=foreground,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=background,
            highlightcolor=style.lookup("TEntry", "focuscolor") or "#4a6984",
        )
        self.prefix_text.grid(row=0, column=0, sticky="nsew")
        prefix_scrollbar = ttk.Scrollbar(prefix_frame, orient="vertical", command=self.prefix_text.yview)
        prefix_scrollbar.grid(row=0, column=1, sticky="ns")
        self.prefix_text.configure(yscrollcommand=prefix_scrollbar.set)
        self.prefix_text.insert("1.0", "\n".join(self.prefixes))
        self.prefix_text.edit_modified(False)

        output_frame = ttk.Frame(container)
        output_frame.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        output_frame.columnconfigure(1, weight=1)
        ttk.Label(output_frame, text="输出目录").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(output_frame, textvariable=self.save_dir_var).grid(
            row=0, column=1, sticky="ew",
        )
        ttk.Button(output_frame, text="浏览…", command=self._browse_output).grid(
            row=0, column=2, padx=(8, 0),
        )
        self.generate_button = ttk.Button(
            output_frame, text="生成二维码", command=self._start_generation,
        )
        self.generate_button.grid(row=0, column=3, padx=(8, 0))

        qr_content_frame = ttk.LabelFrame(
            container, text="本批次二维码内容（每条一行，可选中复制）", padding=6,
        )
        qr_content_frame.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        qr_content_frame.columnconfigure(0, weight=1)
        qr_content_frame.rowconfigure(0, weight=1)
        self.qr_content_text = tk.Text(
            qr_content_frame,
            wrap="char",
            height=3,
            font=font,
            background=field_background,
            foreground=foreground,
            insertbackground=foreground,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=background,
            highlightcolor=style.lookup("TEntry", "focuscolor") or "#4a6984",
            state="disabled",
        )
        self.qr_content_text.grid(row=0, column=0, sticky="nsew")
        qr_content_scrollbar = ttk.Scrollbar(
            qr_content_frame, orient="vertical", command=self.qr_content_text.yview,
        )
        qr_content_scrollbar.grid(row=0, column=1, sticky="ns")
        self.qr_content_text.configure(yscrollcommand=qr_content_scrollbar.set)

        result_frame = ttk.Frame(container)
        result_frame.grid(row=4, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(1, weight=1)
        browser_frame = ttk.Frame(result_frame)
        browser_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(browser_frame, text="浏览器").grid(row=0, column=0, padx=(0, 8))
        ttk.Combobox(
            browser_frame,
            textvariable=self.browser_var,
            values=tuple(BROWSER_PATHS),
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="w")
        ttk.Button(
            browser_frame,
            text="打开所选页面",
            command=self._open_selected_page,
        ).grid(row=0, column=2, padx=(8, 0))
        # 列表保留二维码缩略图，双击结果行后查看放大的二维码和完整信息。
        ttk.Style(self.root).configure("QrResult.Treeview", rowheight=76)
        result_font = style.lookup("QrResult.Treeview", "font") or "TkDefaultFont"
        self.result_font = tkfont.Font(root=self.root, font=result_font)
        self.results = ttk.Treeview(
            result_frame,
            columns=("input", "content", "output"),
            show="tree headings",
            selectmode="extended",
            style="QrResult.Treeview",
            height=3,
        )
        self.results.heading("#0", text="二维码")
        self.results.column("#0", width=84, minwidth=84, stretch=False)
        for column, heading, width in (
            ("input", "生成前输入", 140),
            ("content", "二维码内容 / 地址", 340),
            ("output", "输出文件或错误", 280),
        ):
            self.results.heading(column, text=heading)
            self.results.column(column, width=width, minwidth=90, stretch=True)
        self.results.grid(row=1, column=0, sticky="nsew")
        self.results.bind("<Configure>", self._resize_result_columns)
        self.results.bind("<Double-1>", self._show_result_details)
        result_scrollbar = ttk.Scrollbar(
            result_frame, orient="vertical", command=self.results.yview,
        )
        result_scrollbar.grid(row=1, column=1, sticky="ns")
        self.results.configure(yscrollcommand=result_scrollbar.set)

    def _set_qr_content_text(self, text: str) -> None:
        """更新主窗口中的二维码内容展示框，并保留只读状态。"""
        self.qr_content_text.configure(state="normal")
        self.qr_content_text.delete("1.0", "end")
        self.qr_content_text.insert("1.0", text)
        self.qr_content_text.configure(state="disabled")
        self.qr_content_text.yview_moveto(0)

    def _append_qr_content_text(self, text: str) -> None:
        """将一条已生成二维码的完整载荷追加到可复制的批次列表。"""
        self.qr_content_text.configure(state="normal")
        if self.qr_content_count:
            self.qr_content_text.insert("end", "\n")
        self.qr_content_text.insert("end", text)
        self.qr_content_text.configure(state="disabled")
        self.qr_content_count += 1

    def _resize_result_columns(self, event: tk.Event) -> None:
        """按表格宽度调整列宽，并同步重排已有结果文字。"""
        text_width = max(0, event.width - self.results.column("#0", "width"))
        input_width = int(text_width * 0.18)
        content_width = int(text_width * 0.45)
        output_width = max(0, text_width - input_width - content_width)
        for column, width in (
            ("input", input_width),
            ("content", content_width),
            ("output", output_width),
        ):
            self.results.column(column, width=width)
        for result_id, details in self.result_details.items():
            self.results.item(
                result_id,
                values=(
                    self._wrap_result_text(details["input"], "input"),
                    self._wrap_result_text(details["content"], "content"),
                    self._wrap_result_text(details["output"], "output"),
                ),
            )

    def _wrap_result_text(self, text: str, column: str) -> str:
        """按指定表格列的当前像素宽度换行文本。"""
        available_width = max(1, self.results.column(column, "width") - 8)
        return _wrap_cell_text(text, available_width, self.result_font)

    def _get_prefixes(self) -> list[str]:
        """读取前缀文本框中的非空行，并保留每行原始内容。"""
        return [
            line for line in self.prefix_text.get("1.0", "end-1c").splitlines()
            if line.strip()
        ]

    def _set_prefixes(self, prefixes: list[str]) -> None:
        """用配置组的前缀列表替换文本框内容。"""
        self.prefix_text.configure(state="normal")
        self.prefix_text.delete("1.0", "end")
        self.prefix_text.insert("1.0", "\n".join(prefixes))
        self.prefix_text.edit_modified(False)

    def _on_prefix_text_modified(self, event: tk.Event) -> None:
        """前缀列表编辑完成后安排自动保存。"""
        if self.prefix_text.edit_modified():
            self.prefix_text.edit_modified(False)
            self._schedule_config_save()

    def _select_profile(self, event: tk.Event) -> None:
        """将选中的配置组载入当前控件。"""
        selected = self.profile_var.get()
        profile = next((item for item in self.profiles if item["name"] == selected), None)
        if profile is None:
            return
        self.mode_var.set(profile["mode"])
        self._set_prefixes(profile["prefix"])
        self.profile_name_var.set(profile["name"])
        self._schedule_config_save()

    def _schedule_config_save(self, *_args: str) -> None:
        """延迟保存最近设置，避免每输入一个字符就写入磁盘。"""
        # 连续编辑时取消旧计时，只在输入暂停后写入最后一次状态。
        if self.pending_config_save is not None:
            self.root.after_cancel(self.pending_config_save)
        self.pending_config_save = self.root.after(500, self._save_last_used_config)

    def _save_last_used_config(self) -> None:
        """保存最近使用的模式、前缀和浏览器，不保存多行正文。"""
        self.pending_config_save = None
        self._persist_configuration(self.profiles, self.profile_var.get())

    def _current_configuration(self, profiles: list[Profile], last_profile: str) -> dict[str, object]:
        """组织配置组和最近使用设置，不包含多行输入正文。"""
        # 多行正文可能包含敏感内容，因此只保存模式、前缀和命名配置。
        return {
            "profiles": profiles,
            "last_mode": self.mode_var.get(),
            "last_prefix": self._get_prefixes(),
            "last_profile": last_profile,
            "last_browser": self.browser_var.get(),
        }

    def _persist_configuration(self, profiles: list[Profile], last_profile: str) -> bool:
        """保存配置；失败时通过界面明确报告错误。"""
        try:
            _write_configuration(self._current_configuration(profiles, last_profile))
        except OSError as error:
            messagebox.showerror("配置保存失败", f"无法保存配置：{error}", parent=self.root)
            return False
        self.profiles = profiles
        return True

    def _save_profile(self) -> None:
        """新增或更新一个命名的模式和前缀配置组。"""
        name = self.profile_name_var.get().strip()
        if not name:
            messagebox.showwarning("缺少配置名称", "请输入配置名称。", parent=self.root)
            return
        prefixes = self._get_prefixes()
        profile = Profile(name=name, mode=self.mode_var.get(), prefix=prefixes)
        profiles = list(self.profiles)
        existing_index = next(
            (index for index, item in enumerate(profiles) if item["name"] == name),
            None,
        )
        if existing_index is None:
            profiles.append(profile)
        else:
            profiles[existing_index] = profile
        if not self._persist_configuration(profiles, name):
            return
        self.profile_var.set(name)
        self.profile_choice.configure(values=[item["name"] for item in profiles])

    def _delete_profile(self) -> None:
        """经用户确认后删除当前选中的配置组。"""
        name = self.profile_var.get()
        if not any(item["name"] == name for item in self.profiles):
            messagebox.showinfo("没有选中配置", "请选择要删除的配置组。", parent=self.root)
            return
        if not messagebox.askyesno("删除配置", f"确定删除配置“{name}”吗？", parent=self.root):
            return
        profiles = [item for item in self.profiles if item["name"] != name]
        if not self._persist_configuration(profiles, ""):
            return
        self.profile_var.set("")
        self.profile_name_var.set("")
        self.mode_var.set("raw")
        self._set_prefixes([])
        self.profile_choice.configure(values=[item["name"] for item in profiles])

    def _browse_output(self) -> None:
        """通过系统目录选择框设置二维码输出目录。"""
        selected = filedialog.askdirectory(
            parent=self.root,
            initialdir=self.save_dir_var.get() or str(Path.home()),
        )
        if selected:
            self.save_dir_var.set(selected)

    def _start_generation(self) -> None:
        """校验输入，并让每行内容与配置组中的所有前缀分别组合生成。"""
        lines = self.input_text.get("1.0", "end-1c").splitlines()
        # 仅用 strip 判断空行，其他行保留原始首尾空格作为二维码内容。
        input_lines = [line for line in lines if line.strip()]
        if not input_lines:
            messagebox.showwarning("没有输入内容", "请在多行文本框中输入内容，每行生成一个二维码。", parent=self.root)
            return
        save_dir = self.save_dir_var.get().strip()
        if not save_dir:
            messagebox.showwarning("缺少输出目录", "请选择二维码输出目录。", parent=self.root)
            return

        self.job_mode = self.mode_var.get()
        self.job_save_dir = Path(save_dir).expanduser()
        if self.job_mode == "prefix":
            prefixes = self._get_prefixes()
            if not prefixes:
                messagebox.showwarning("缺少固定前缀", "请在前缀列表中至少添加一项。", parent=self.root)
                return
            # 每行输入与每个前缀组合，生成结果直接写入主窗口表格。
            self.job_lines = []
            self.job_prefixes = []
            for line in input_lines:
                for prefix in prefixes:
                    self.job_lines.append(line)
                    self.job_prefixes.append(prefix)
        else:
            self.job_lines = input_lines
            self.job_prefixes = [""] * len(input_lines)
        self._begin_generation()

    def _begin_generation(self) -> None:
        """清理旧结果并开始逐条生成二维码。"""
        self.job_index = 0
        # 批次使用启动时的配置快照，后续控件变化不会影响已排队任务。
        self.results.delete(*self.results.get_children())
        self.qr_images.clear()
        self.result_details.clear()
        self.qr_content_count = 0
        self._set_qr_content_text("")
        self.generate_button.state(["disabled"])
        # 分条交还事件循环，窗口可持续刷新进度和响应输入。
        self.root.after(1, self._generate_next)

    def _generate_next(self) -> None:
        """生成一条二维码并安排下一条任务，避免批量处理阻塞窗口。"""
        if self.job_index >= len(self.job_lines):
            self.generate_button.state(["!disabled"])
            return

        line = self.job_lines[self.job_index]
        # URL 模式在二维码上方绘制路径说明；原样模式只编码输入内容。
        prefix = self.job_prefixes[self.job_index]
        content = prefix + line if self.job_mode == "prefix" else line
        cache_key = (self.job_mode, str(self.job_save_dir.resolve()), content)
        thumbnail = None
        image_path = ""
        try:
            output_path = self.qr_cache.get(cache_key)
            if output_path is None or not output_path.is_file():
                output_path = _create_qr_image(
                    content,
                    self.job_save_dir,
                    show_url_text=self.job_mode == "prefix",
                )
                # 只缓存当前会话的输出路径，不把输入内容写入配置文件。
                self.qr_cache[cache_key] = output_path
        except Exception as error:
            result = f"失败：{error}"
        else:
            result = str(output_path)
            image_path = str(output_path)
            try:
                with Image.open(output_path) as qr_image:
                    thumbnail_image = qr_image.convert("RGB")
                resampling = getattr(Image, "Resampling", Image)
                thumbnail_image.thumbnail((68, 68), resampling.NEAREST)
                thumbnail = ImageTk.PhotoImage(thumbnail_image, master=self.root)
                # Tk 图片需保留 Python 引用，否则 Treeview 中的缩略图会消失。
                self.qr_images.append(thumbnail)
            except OSError as error:
                # 二维码文件已生成；预览读取失败不改变生成成功计数。
                result = f"{output_path}（预览失败：{error}）"
        # 列表使用换行副本；原始输入和最终载荷单独保存供详情窗口完整显示。
        result_id = self.results.insert(
            "",
            "end",
            image=thumbnail,
            values=(
                self._wrap_result_text(line, "input"),
                self._wrap_result_text(content, "content"),
                self._wrap_result_text(result, "output"),
            ),
        )
        self.result_details[result_id] = {
            "input": line,
            "content": content,
            "output": result,
            "image": image_path,
        }
        if image_path:
            self._append_qr_content_text(content)
        self.job_index += 1
        self.root.after(1, self._generate_next)

    def _show_result_details(self, event: tk.Event) -> None:
        """双击结果行时显示放大的二维码和该行完整信息。"""
        result_id = self.results.identify_row(event.y)
        if not result_id:
            return
        self.results.selection_set(result_id)
        self.results.focus(result_id)
        details = self.result_details.get(result_id)
        if details is None:
            return

        if self.preview_window is None or not self.preview_window.winfo_exists():
            self._create_preview_window()
        # 复用同一个详情窗，连续选择结果时只更新其中的图片和文字。
        self._update_preview_window(details)

    def _open_selected_page(self) -> None:
        """使用所选浏览器打开所有选中结果中的有效 HTTP 或 HTTPS 地址。"""
        selected = self.results.selection()
        if not selected:
            messagebox.showinfo("未选择结果", "请先在表格中选择一行。", parent=self.root)
            return
        urls = []
        invalid_count = 0
        for result_id in selected:
            details = self.result_details.get(result_id)
            if details is None:
                invalid_count += 1
                continue
            url = details["content"]
            try:
                parsed_url = urlsplit(url)
            except ValueError:
                invalid_count += 1
                continue
            if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.hostname:
                invalid_count += 1
                continue
            urls.append(url)

        if not urls:
            messagebox.showwarning(
                "无法打开页面",
                "所选行中没有有效的 HTTP 或 HTTPS 地址。",
                parent=self.root,
            )
            return

        browser_name = self.browser_var.get()
        executable = _find_browser_executable(browser_name)
        if executable is None:
            messagebox.showerror(
                "浏览器未安装",
                f"未找到 {browser_name}，请安装该浏览器后重试。",
                parent=self.root,
            )
            return

        failures = []
        for index, url in enumerate(urls, start=1):
            try:
                subprocess.Popen([str(executable), url])
            except OSError as error:
                failures.append(f"第 {index} 个页面：{error}")
        if failures or invalid_count:
            message = f"已打开 {len(urls) - len(failures)} 个页面。"
            if invalid_count:
                message += f"\n跳过 {invalid_count} 行无效地址。"
            if failures:
                message += "\n" + "\n".join(failures)
            show_message = messagebox.showerror if failures else messagebox.showwarning
            show_message(
                "部分页面未打开",
                message,
                parent=self.root,
            )

    def _create_preview_window(self) -> None:
        """创建仅展示当前表格行信息的上下布局详情窗口。"""
        self.preview_window = tk.Toplevel(self.root)
        self.preview_window.title("当前行二维码详情")
        window_width = max(520, min(820, self.root.winfo_screenwidth() - 80))
        window_height = max(360, min(720, self.root.winfo_screenheight() - 80))
        _center_window(self.preview_window, window_width, window_height)
        self.preview_window.minsize(min(520, window_width), min(360, window_height))
        self.preview_window.columnconfigure(0, weight=1)
        self.preview_window.rowconfigure(0, weight=1)

        self.preview_content = ttk.Frame(self.preview_window, padding=12)
        self.preview_content.grid(row=0, column=0, sticky="nsew")
        self.preview_content.columnconfigure(0, weight=1)
        self.preview_image_label = ttk.Label(self.preview_content, anchor="center")
        self.preview_image_label.grid(row=0, column=0, sticky="n", pady=(0, 6))

        details_frame = ttk.LabelFrame(self.preview_content, text="当前行信息", padding=10)
        details_frame.grid(row=1, column=0, sticky="ew")
        details_frame.columnconfigure(0, weight=1)
        details_frame.bind("<Configure>", self._resize_preview_text)
        self.preview_values.clear()
        for row, title in enumerate(("原始输入", "二维码内容 / 地址", "输出文件")):
            ttk.Label(details_frame, text=title).grid(row=row * 2, column=0, sticky="w")
            value = ttk.Label(details_frame, justify="left", anchor="w")
            value.grid(row=row * 2 + 1, column=0, sticky="ew", pady=(2, 10))
            self.preview_values.append(value)

    def _resize_preview_text(self, event: tk.Event) -> None:
        """根据详情窗口宽度调整文本换行，避免内容被裁切。"""
        wrap_width = max(160, event.width - 24)
        for label in self.preview_values:
            label.configure(wraplength=wrap_width)

    def _update_preview_window(self, details: dict[str, str]) -> None:
        """更新详情窗口中的二维码图片和完整文本信息。"""
        if self.preview_window is None:
            return
        if details["image"]:
            try:
                with Image.open(details["image"]) as qr_image:
                    preview_image = qr_image.convert("RGB")
            except OSError as error:
                self.preview_photo = None
                self.preview_image_label.configure(image="", text=f"无法读取二维码：{error}")
            else:
                max_size = max(
                    160,
                    min(
                        560,
                        self.root.winfo_screenheight() - 460,
                        self.root.winfo_screenwidth() - 100,
                    ),
                )
                resampling = getattr(Image, "Resampling", Image)
                # 仅缩放预览副本，磁盘上的二维码文件保持原始尺寸。
                preview_image.thumbnail((max_size, max_size), resampling.NEAREST)
                self.preview_photo = ImageTk.PhotoImage(preview_image, master=self.preview_window)
                self.preview_image_label.configure(image=self.preview_photo, text="")
        else:
            self.preview_photo = None
            self.preview_image_label.configure(image="", text="二维码生成失败")

        for label, key in zip(self.preview_values, ("input", "content", "output")):
            label.configure(text=details[key])

        self.preview_window.update_idletasks()
        window_height = min(
            max(360, self.preview_content.winfo_reqheight() + 30),
            max(360, self.root.winfo_screenheight() - 80),
        )
        _center_window(self.preview_window, self.preview_window.winfo_width(), window_height)
        self.preview_window.lift()

    def _close(self) -> None:
        """关闭前保存最近使用的配置。"""
        if self.pending_config_save is not None:
            self.root.after_cancel(self.pending_config_save)
            self.pending_config_save = None
        self._persist_configuration(self.profiles, self.profile_var.get())
        self.root.destroy()


def launch_qr_gui(
    title: str,
    default_save_dir: Path,
) -> None:
    """启动独立实现二维码生成逻辑的 GUI。"""
    root = tk.Tk()
    root.withdraw()
    try:
        configuration = _load_configuration()
    except (OSError, ValueError) as error:
        messagebox.showerror("配置读取失败", str(error), parent=root)
        root.destroy()
        return
    root.deiconify()
    QrGeneratorApp(root, title, default_save_dir, configuration)
    root.mainloop()


def main() -> None:
    """启动独立实现 URL 和原样文本二维码生成的 GUI。"""
    parser = argparse.ArgumentParser(description="Self-contained URL and raw-text QR generator GUI")
    parser.add_argument(
        "--save-dir",
        "-d",
        type=Path,
        help="Initial output directory; defaults to the GUI folder's output directory",
    )
    args = parser.parse_args()

    default_save_dir = args.save_dir or Path(__file__).resolve().parent / "output"
    launch_qr_gui(
        title="URL 与文本二维码生成器",
        default_save_dir=default_save_dir,
    )


if __name__ == "__main__":
    main()
