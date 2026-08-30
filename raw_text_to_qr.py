#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原样字符串二维码工具。

传入的字符串不会被 strip、解析、补充认证信息或改写，直接作为二维码内容。
仅输出文件名会沿用原工具的转换规则。

用法：
    python raw_text_to_qr.py "<任意文本或 URL>"
    python raw_text_to_qr.py
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import qrcode

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SAVE_DIR = BASE_DIR / "output" / "QR_raw"


def sanitize_filename(name: str, max_len: int = 200) -> str:
    """与 url_to_qr.py 保持一致的文件名转换规则。"""
    if not name:
        return "unnamed"
    name = unquote(name)
    name = name.replace("/", "_").replace("\\\\", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip(" _.")
    if not name:
        return "unnamed"
    return name[:max_len]


def extract_name_from_url(url: str) -> str:
    """与 url_to_qr.py 保持一致地从输入内容提取文件名主体。"""
    if not url:
        return "unnamed"
    parsed = urlparse(url)
    parts = parsed.path or ""
    if parsed.query:
        parts += "?" + parsed.query
    if parsed.fragment:
        parts += "#" + parsed.fragment
    parts = re.sub(r"^[/:?#]+", "", parts)
    return parts or "root"


def build_timestamped_filename(file_name: str, ext: str) -> str:
    """与 url_to_qr.py 保持一致地添加时间戳与扩展名。"""
    base_name = file_name
    if file_name.lower().endswith("." + ext):
        base_name = file_name[:-(len(ext) + 1)]
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sanitize_filename(base_name)}.{ext}"


def build_output_path(save_dir: Path, text: str) -> Path:
    """按原工具的命名规则创建不会覆盖已有文件的输出路径。"""
    file_name = build_timestamped_filename(extract_name_from_url(text), "png")
    candidate = save_dir / file_name
    stem = candidate.stem
    counter = 2
    while candidate.exists():
        candidate = save_dir / f"{stem}_{counter:02d}.png"
        counter += 1
    return candidate


def text_to_qr(text: str, save_dir: Path | str = DEFAULT_SAVE_DIR) -> Path:
    """将 text 原样写入二维码；文件名使用原工具的转换规则。"""
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    qr = qrcode.QRCode(box_size=12, border=4)
    qr.add_data(text)
    qr.make(fit=True)

    output_path = build_output_path(save_dir, text)
    qr.make_image(fill_color="black", back_color="white").save(output_path)
    return output_path


def open_image(output_path: Path) -> None:
    """使用系统默认图片查看器打开生成的二维码。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(output_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(output_path)], check=False)
        else:
            subprocess.run(["xdg-open", str(output_path)], check=False)
    except OSError as error:
        print(f"无法自动打开图片: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="原样字符串 -> 二维码（不处理输入内容）")
    parser.add_argument("texts", nargs="*", help="要生成二维码的字符串；每个参数生成一个二维码")
    parser.add_argument(
        "--save-dir",
        "-d",
        type=Path,
        default=DEFAULT_SAVE_DIR,
        help="输出目录，默认：脚本目录下 output/QR_raw",
    )
    args = parser.parse_args()

    if args.texts:
        for text in args.texts:
            output_path = text_to_qr(text, args.save_dir)
            print(f"已生成: {output_path}")
            open_image(output_path)
        return

    print("请输入字符串；内容会原样写入二维码。输入单独一行 exit 或 quit 退出。")
    while True:
        try:
            text = input("> ")
        except EOFError:
            print()
            return

        # 只将完全等于退出命令的一行视为命令；其余输入（包括前后空格）原样保留。
        if text in {"exit", "quit"}:
            return

        output_path = text_to_qr(text, args.save_dir)
        print(f"已生成: {output_path}")
        open_image(output_path)


if __name__ == "__main__":
    main()

