"""Quality gate for unpublished REIGN pose assets. Requires Pillow at build time."""
import hashlib
import json
import math
import tempfile
from pathlib import Path

def sharpness(image):
    from PIL import ImageFilter, ImageStat
    if min(image.size)<128: raise ValueError("portrait too small")
    box=(round(image.width*.23),round(image.height*.16),round(image.width*.77),round(image.height*.74))
    roi=image.convert("L").crop(box)
    # Exclude high-pass filter border; otherwise even a blank image scores high.
    edges=roi.filter(ImageFilter.FIND_EDGES).crop((2,2,roi.width-2,roi.height-2))
    return float(ImageStat.Stat(edges).var[0])

def inspect(frames,source,minimum=36.0,ratio=.55):
    baseline=sharpness(source)
    if baseline<minimum or not math.isfinite(baseline):
        raise ValueError("original source is blurry")
    frames=list(frames)
    if not frames:raise ValueError("no frames")
    items=[]
    for name,frame in frames:
        if not name or any(char in name for char in "/\\") or name in (".",".."):
            raise ValueError("unsafe frame name")
        if frame.size!=source.size:raise ValueError("dimensions differ: cannot upscale")
        score=sharpness(frame)
        ok=math.isfinite(score) and score>=minimum and score>=baseline*ratio
        items.append({"name":name,"sharpness":round(score,2),"relative":round(score/baseline,4),"pass":bool(ok)})
    return {"pass":all(x["pass"] for x in items),
            "failed":[x["name"] for x in items if not x["pass"]],
            "source_sharpness":round(baseline,2),"frames":items,
            "requires_human_review":True}

def contact_sheet(frames,path):
    from PIL import Image,ImageDraw
    frames=list(frames)
    if not frames:raise ValueError("no frames")
    out=Image.new("RGB",(1024,286*math.ceil(len(frames)/4)),"#202020")
    draw=ImageDraw.Draw(out)
    for i,(name,frame) in enumerate(frames):
        if frame.size!=(256,256):raise ValueError("use native pixels")
        x=i%4*256;y=i//4*286
        out.paste(frame.convert("RGB"),(x,y))
        draw.text((x+5,y+260),name[:35],fill="white")
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    out.save(path,"PNG")

def stage(frames,original,destination,model_hashes=None):
    """Only stages versioned candidates, never modifies production images."""
    frames=list(frames);dest=Path(destination)
    if dest.exists():raise FileExistsError("candidate already exists")
    report=inspect(frames,original)
    if not report["pass"]:
        raise ValueError("blur gate failed: "+", ".join(report["failed"]))
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as d:
        temp=Path(d)/"candidate";temp.mkdir()
        for name,frame in frames:frame.save(temp/(name+".png"),"PNG")
        contact_sheet(frames,temp/"review.png")
        (temp/"quality.json").write_text(json.dumps({
            **report,"original_sha256":hashlib.sha256(original.tobytes()).hexdigest(),
            "models":model_hashes or {}},indent=2)+"\n")
        if dest.exists():raise FileExistsError("candidate already exists")
        temp.rename(dest)
    return report
