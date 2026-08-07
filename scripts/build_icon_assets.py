from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCES = PROJECT_ROOT / "Resources"
ICON_SETS = {
    "IconMasters/BEMExport.png": "MSHExport",
    "IconMasters/BEMQuickExport.png": "MSHQuickExport",
    "IconMasters/FEMExport.png": "MSHFEMExport",
    "IconMasters/FEMQuickExport.png": "MSHQuickFEMExport",
}
ICON_SIZES = (16, 32, 64)


def render_icon(master_path, destination, size):
    with Image.open(master_path) as source:
        source = source.convert("RGBA")
        alpha_box = source.getchannel("A").getbbox()
        if alpha_box is None:
            raise ValueError(f"Icon master has no visible pixels: {master_path}")

        source = source.crop(alpha_box)
        padding = max(1, round(size * 0.047))
        available = size - 2 * padding
        scale = min(available / source.width, available / source.height)
        dimensions = (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        )
        source = source.resize(dimensions, Image.Resampling.LANCZOS)

        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        position = ((size - source.width) // 2, (size - source.height) // 2)
        canvas.alpha_composite(source, position)
        canvas.save(destination, format="PNG", optimize=True)


def main():
    for master_name, resource_folder in ICON_SETS.items():
        master_path = RESOURCES / master_name
        destination_folder = RESOURCES / resource_folder
        destination_folder.mkdir(parents=True, exist_ok=True)
        for size in ICON_SIZES:
            destination = destination_folder / f"{size}x{size}.png"
            render_icon(master_path, destination, size)
            print(destination.relative_to(PROJECT_ROOT))


if __name__ == "__main__":
    main()
