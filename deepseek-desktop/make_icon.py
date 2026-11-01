from PIL import Image, ImageDraw, ImageFont
import os

base = r"D:\Users\Administrator\Documents\deepseek-harness\default-workspace\deepseek-desktop\assets"
os.makedirs(base, exist_ok=True)

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 蓝色圆角方形背景
radius = 96
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=(29, 78, 216, 255))  # 蓝色

# 画一条简化的鲸鱼轮廓（白色）
cx, cy = S // 2, S // 2
# 身体
d.ellipse([cx - 130, cy - 60, cx + 60, cy + 70], fill=(255, 255, 255, 255))
# 尾巴（右侧）
d.polygon([
    (cx + 55, cy - 20),
    (cx + 140, cy - 60),
    (cx + 150, cy - 40),
    (cx + 60, cy + 5),
], fill=(255, 255, 255, 255))
d.polygon([
    (cx + 55, cy + 20),
    (cx + 140, cy + 60),
    (cx + 150, cy + 40),
    (cx + 60, cy - 5),
], fill=(255, 255, 255, 255))
# 眼睛
d.ellipse([cx - 95, cy - 28, cx - 65, cy + 2], fill=(29, 78, 216, 255))
# 喷水
d.ellipse([cx - 120, cy - 120, cx - 80, cy - 80], fill=(255, 255, 255, 255))

png = os.path.join(base, "icon.png")
img.save(png)
print("saved", png)

# 生成多尺寸 ico
sizes = [16, 24, 32, 48, 64, 128, 256]
ico = os.path.join(base, "icon.ico")
img.save(ico, sizes=[(s, s) for s in sizes])
print("saved", ico)
