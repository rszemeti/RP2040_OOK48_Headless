from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def make_icon(target: Path) -> None:
    size = 256
    img = Image.new("RGBA", (size, size), (14, 18, 26, 255))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle((10, 10, 246, 246), radius=36, outline=(255, 120, 0, 255), width=8)

    bars = [52, 98, 68, 136, 92, 160, 112, 84]
    x = 28
    for h in bars:
        d.rounded_rectangle((x, 200 - h, x + 16, 200), radius=5, fill=(0, 220, 170, 255))
        x += 24

    text = "OOK48"
    try:
        font = ImageFont.truetype("arialbd.ttf", 34)
    except Exception:
        font = ImageFont.load_default()

    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    d.text(((size - tw) / 2, 26), text, fill=(255, 255, 255, 255), font=font)

    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    make_icon(repo_root / "assets" / "ook48.ico")
    print("Created assets/ook48.ico")
