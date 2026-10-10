"""Server and disk information, formatted as Telegram-HTML text.

Uses only the standard library (/proc on Linux, with graceful fallbacks).
"""
import html
import os
import platform
import shutil
import socket
import time

import config
from storage import human_size

# File-system types that are real disks (overlay/tmpfs/proc... are skipped).
_REAL_FS = {
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "jfs", "reiserfs",
    "vfat", "exfat", "ntfs", "ntfs3", "fuseblk",
}


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def bar(percent, width=10):
    percent = max(0.0, min(100.0, percent))
    filled = int(round(percent / 100 * width))
    return "▓" * filled + "░" * (width - filled)


def percent_used(usage):
    """Same percentage that ``df`` shows."""
    denom = usage.used + usage.free
    return usage.used / denom * 100 if denom else 0.0


def fmt_duration(seconds):
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days} روز")
    if hours:
        parts.append(f"{hours} ساعت")
    parts.append(f"{minutes} دقیقه")
    return "، ".join(parts)


def hostname():
    return socket.gethostname()


def os_name():
    text = _read("/etc/os-release")
    if text:
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return platform.platform()


def cpu_model():
    text = _read("/proc/cpuinfo")
    if text:
        for line in text.splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "نامشخص"


def memory():
    """Return a dict of /proc/meminfo values in bytes, or None."""
    text = _read("/proc/meminfo")
    if not text:
        return None
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    return values


def uptime_seconds():
    text = _read("/proc/uptime")
    try:
        return float(text.split()[0])
    except (AttributeError, IndexError, ValueError):
        return None


def list_disks():
    """Mounted real disks, one entry per device:
    ``[{"device", "fstype", "usage", "mounts"}, ...]``."""
    groups, order = {}, []
    for line in (_read("/proc/mounts") or "").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        # skip virtual file systems and single-file mounts (/etc/hosts, ...)
        if fstype not in _REAL_FS or not os.path.isdir(mount):
            continue
        if device not in groups:
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            groups[device] = {"device": device, "fstype": fstype, "usage": usage, "mounts": []}
            order.append(device)
        groups[device]["mounts"].append(mount)
    result = [groups[d] for d in order]
    if not result:
        try:
            result.append(
                {"device": "", "fstype": "", "usage": shutil.disk_usage("/"), "mounts": ["/"]}
            )
        except OSError:
            pass
    return result


def _usage_block(title, usage, subtitle=""):
    pct = percent_used(usage)
    lines = [f"<b>{title}</b>"]
    if subtitle:
        lines.append(subtitle)
    lines.append(f"{bar(pct)} {pct:.0f}%")
    lines.append(f"استفاده‌شده: {human_size(usage.used)} از {human_size(usage.total)}")
    lines.append(f"آزاد: <b>{human_size(usage.free)}</b>")
    return "\n".join(lines)


def _dev(path):
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def disk_report(save_path, with_time=True):
    parts = ["💾 <b>وضعیت دیسک</b>"]

    save_dev = _dev(save_path)
    try:
        usage = shutil.disk_usage(save_path)
        parts.append(
            _usage_block(
                "📂 دیسکِ مسیر ذخیره", usage, f"<code>{html.escape(save_path)}</code>"
            )
        )
    except OSError:
        parts.append(f"⚠️ مسیر ذخیره در دسترس نیست: <code>{html.escape(save_path)}</code>")

    staging = config.API_HOST_DIR if config.USES_LOCAL_API else ""
    staging_dev = _dev(staging) if staging else None

    disks = list_disks()
    if disks:
        parts.append("<b>همه‌ی دیسک‌ها</b>")
        for disk in disks:
            first = disk["mounts"][0]
            dev = _dev(first)
            mark = ""
            if save_dev is not None and dev == save_dev:
                mark += " 📌"
            if staging_dev is not None and dev == staging_dev:
                mark += " 📥"
            info = []
            if disk["device"]:
                info.append(f"دیوایس: <code>{html.escape(disk['device'])}</code>")
            if disk["fstype"]:
                info.append(f"نوع: <code>{html.escape(disk['fstype'])}</code>")
            lines = [" • ".join(info)] if info else []
            if len(disk["mounts"]) > 1:
                shown = ", ".join(html.escape(m) for m in disk["mounts"][:4])
                lines.append(f"mount: {shown}")
            parts.append(
                _usage_block(f"💽 {html.escape(first)}{mark}", disk["usage"], "\n".join(lines))
            )
        parts.append("📌 دیسک مسیر ذخیره   📥 دیسک داده‌ی Bot API")

    if with_time:
        parts.append(f"🕒 {time.strftime('%H:%M:%S')}")
    return "\n\n".join(parts)


def server_report(save_path):
    lines = [
        "🖥 <b>اطلاعات سرور</b>",
        "",
        f"🏷 نام: <code>{html.escape(hostname())}</code>",
        f"🐧 سیستم‌عامل: {html.escape(os_name())}",
        f"⚙️ هسته: {html.escape(platform.release())} ({html.escape(platform.machine())})",
        f"🧠 پردازنده: {html.escape(cpu_model())} — {os.cpu_count() or '?'} هسته",
    ]

    try:
        l1, l5, l15 = os.getloadavg()
        lines.append(f"📈 بار سیستم (۱/۵/۱۵ دقیقه): {l1:.2f} / {l5:.2f} / {l15:.2f}")
    except (OSError, AttributeError):
        pass

    mem = memory()
    if mem and mem.get("MemTotal"):
        total = mem["MemTotal"]
        used = total - mem.get("MemAvailable", mem.get("MemFree", 0))
        pct = used / total * 100
        lines.append(
            f"🧮 رم: {bar(pct)} {pct:.0f}% ({human_size(used)} از {human_size(total)})"
        )
        swap_total = mem.get("SwapTotal", 0)
        if swap_total:
            swap_used = swap_total - mem.get("SwapFree", 0)
            lines.append(
                f"🔁 Swap: {human_size(swap_used)} از {human_size(swap_total)}"
            )

    up = uptime_seconds()
    if up is not None:
        lines.append(f"⏳ آپ‌تایم: {fmt_duration(up)}")
    lines.append(f"🐍 پایتون: {platform.python_version()}")

    return "\n".join(lines) + "\n\n" + disk_report(save_path, with_time=False) + (
        f"\n\n🕒 {time.strftime('%H:%M:%S')}"
    )
