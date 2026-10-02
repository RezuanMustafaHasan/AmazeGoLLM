"""The exact labelled board image supplied to models and displayed to admins."""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

CELL = 36
PAD = 32


def render_board(state):
    width = (state["level"]["columns"] - 1) * CELL + PAD * 2
    height = (state["level"]["rows"] - 1) * CELL + PAD * 2
    image = Image.new("RGB", (width, height), "#fcfcf8")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=12)

    def point(cell):
        return (PAD + cell[1] * CELL, PAD + cell[0] * CELL)

    for row, cells in enumerate(state["matrix"]):
        for col, value in enumerate(cells):
            x, y = point((row, col))
            if value == -1:
                draw.rectangle(
                    (x - CELL // 2, y - CELL // 2, x + CELL // 2, y + CELL // 2), fill="#eef0ec"
                )
            else:
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill="#d9ded6")

    for arrow in state["arrows"]:
        points = [point(cell) for cell in arrow["path"]]
        draw.line(points, fill="#243f35", width=3, joint="curve")
        x, y = points[-1]
        px, py = points[-2]
        dx, dy = (x - px) // CELL, (y - py) // CELL
        draw.polygon(
            [
                (x + dx * 5, y + dy * 5),
                (x - dx * 6 - dy * 5, y - dy * 6 + dx * 5),
                (x - dx * 6 + dy * 5, y - dy * 6 - dx * 5),
            ],
            fill="#243f35",
        )

    # IDs sit at the tails, so labels never hide the direction at an arrowhead.
    for arrow in state["arrows"]:
        x, y = point(arrow["path"][0])
        draw.rounded_rectangle(
            (x - 15, y - 10, x + 15, y + 10), radius=4, fill="#ffffff", outline="#a9bdb1"
        )
        draw.text((x, y), str(arrow["id"]), font=font, fill="#183d2d", anchor="mm")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
