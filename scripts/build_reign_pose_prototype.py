"""Build genuine LP-Distilled pose samples for review; never deploy directly."""
import argparse
import hashlib
import json
from pathlib import Path
from scripts.portrait_quality import inspect,contact_sheet,stage

ROOT=Path(__file__).resolve().parents[1]
POSES=(
 ("neutral",0,0,0,0),("yaw_left_3",-3,0,0,0),
 ("yaw_right_3",3,0,0,0),("yaw_left_4",-4,0,0,0),
 ("yaw_right_4",4,0,0,0),("nod_down_4",0,-4,0,0),
 ("nod_up_4",0,4,0,0),("tilt_left_3",0,0,-3,0),
 ("tilt_right_3",0,0,3,0),("mouth_mid",0,0,0,.45),
 ("mouth_open",0,0,0,.9)
)

def model_paths(directory):
    manifest=json.loads((ROOT/"services/ax/portrait-assets.json").read_text())["assets"]
    result={}
    for name in ("appearance","distilled"):
        item=next(x for x in manifest if x["path"]=="models/"+name+".onnx")
        path=Path(directory)/(name+".onnx")
        if not path.is_file() or path.stat().st_size!=item["size"]:
            raise ValueError(name+": missing/invalid model")
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError(name+": model checksum mismatch")
        result[name]=path
    return result

def generate(directory):
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    ort.disable_telemetry_events()
    paths=model_paths(directory)
    opts=ort.SessionOptions();opts.intra_op_num_threads=4
    enc=ort.InferenceSession(str(paths["appearance"]),opts,providers=["CPUExecutionProvider"])
    model=ort.InferenceSession(str(paths["distilled"]),opts,providers=["CPUExecutionProvider"])
    with Image.open(ROOT/"services/ax/avatar-assets/sample-portrait.png") as original:
        side=min(original.size)
        x=(original.width-side)//2;y=(original.height-side)//2
        source=original.convert("RGB").crop((x,y,x+side,y+side)).resize((256,256),Image.Resampling.LANCZOS)
    rgb=np.asarray(source,dtype=np.float32).transpose(2,0,1)[None]/255
    feature=enc.run(None,{"img":rgb})[0]
    frames=[]
    for name,yaw,pitch,roll,mouth in POSES:
        expr=np.zeros((1,30),dtype=np.float32)
        for i,m in {25:.4375,28:-.2875,23:.1125,16:-.0625}.items():
            expr[0,i]=m*mouth
        pose=np.array([[yaw/20,pitch/14,roll/14]],dtype=np.float32)
        result=model.run(None,{"feature_3d":feature,"pose_control_vector":pose,
                               "input_rgb":rgb,"expression_control_vector":expr})[0]
        if result.shape!=(1,3,256,256) or not np.isfinite(result).all():
            raise ValueError(name+": bad inference")
        frame=Image.fromarray(np.clip(result[0].transpose(1,2,0)*255,0,255).astype(np.uint8))
        frames.append((name,frame))
    return source,frames,{k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--models",required=True)
    p.add_argument("--review",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    if Path(a.review).resolve()==Path(a.output).resolve():p.error("review and output must differ")
    original,frames,hashes=generate(a.models)
    review=Path(a.review);review.mkdir(parents=True,exist_ok=True)
    contact_sheet(frames,review/"raw-poses.png")
    report=inspect(frames,original)
    (review/"quality.json").write_text(json.dumps(report,indent=2)+"\n")
    if not report["pass"]:
        raise SystemExit("FAIL: blurry facial frames detected; no candidate published")
    stage(frames,original,a.output,hashes)
    print("Automated pass; human facial-detail review still required")

if __name__=="__main__":main()
