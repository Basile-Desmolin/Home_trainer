"""Dessine l'icône de l'appli (profil de séance coloré par zones) : PNG et ICO Windows.

    pip install pillow
    python packaging/make_icon.py

Écrit src/home_trainer/ui/assets/icon.png (fenêtre) et icon.ico (exécutable, raccourcis).
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
OUT = Path(__file__).resolve().parents[1] / "src" / "home_trainer" / "ui" / "assets"

BG = (22, 24, 29)
BORDER = (255, 210, 74)
# (largeur relative, hauteur relative, couleur de zone) : échauffement, sweet spot, VO2, retour au calme.
BARS = [(1.2, 0.30, (127, 140, 154)), (1.0, 0.45, (61, 139, 217)), (1.4, 0.70, (231, 196, 58)),
        (0.6, 0.38, (61, 139, 217)), (1.4, 0.70, (231, 196, 58)), (0.6, 0.38, (61, 139, 217)),
        (0.8, 0.92, (229, 72, 77)), (1.2, 0.30, (127, 140, 154))]


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 40
    d.rounded_rectangle((pad, pad, SIZE - pad, SIZE - pad), radius=200, fill=BG, outline=BORDER, width=36)
    left, right, bottom, top = 170, SIZE - 170, SIZE - 210, 200
    total = sum(w for w, _, _ in BARS)
    gap = 14
    x = left
    for w, h, color in BARS:
        width = (right - left) * w / total
        y = bottom - (bottom - top) * h
        d.rounded_rectangle((x + gap / 2, y, x + width - gap / 2, bottom), radius=18, fill=color)
        x += width
    # Ligne de FTP, comme dans le profil de l'appli.
    ftp_y = bottom - (bottom - top) * 0.62
    for x0 in range(left, right, 70):
        d.line((x0, ftp_y, min(x0 + 38, right), ftp_y), fill=(232, 234, 237), width=12)
    d.rounded_rectangle((left - 10, bottom + 30, right + 10, bottom + 62), radius=16, fill=BORDER)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"icônes écrites dans {OUT}")


if __name__ == "__main__":
    main()
