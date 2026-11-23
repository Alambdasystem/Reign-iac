"""Synthetic blur tests: real model faces still require human review."""
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image,ImageDraw,ImageFilter
from scripts.portrait_quality import sharpness,inspect,contact_sheet,stage
from scripts.build_reign_pose_prototype import POSES,choose_sharp_pose

def face():
    im=Image.new("RGB",(256,256),"#aaaaaa");d=ImageDraw.Draw(im)
    for x in range(50,210,3):
        for y in range(42,190,3):
            d.rectangle((x,y,x+1,y+1),fill="#222222" if (x+y)%2 else "#fefefe")
    return im

class ClarityTests(unittest.TestCase):
    def test_rejects_blurred_face(self):
        a=face();b=a.filter(ImageFilter.GaussianBlur(7))
        self.assertGreater(sharpness(a),sharpness(b)*2)
        report=inspect([("good",a),("blurred",b)],a)
        self.assertFalse(report["pass"])
        self.assertEqual(report["failed"],["blurred"])
    def test_rejects_flat_source(self):
        im=Image.new("RGB",(256,256),"gray")
        self.assertEqual(sharpness(im),0)
        with self.assertRaisesRegex(ValueError,"blurry"):
            inspect([("flat",im)],im)
    def test_rejects_fake_512(self):
        im=face()
        with self.assertRaisesRegex(ValueError,"dimensions"):
            inspect([("fake",im.resize((512,512)))],im)
    def test_bad_frame_never_published(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/"new";im=face()
            with self.assertRaisesRegex(ValueError,"blur gate"):
                stage([("bad",im.filter(ImageFilter.GaussianBlur(9)))],im,dest)
            self.assertFalse(dest.exists())
    def test_candidate_requires_manual_review(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/"new";im=face();result=stage([("neutral",im)],im,dest)
            self.assertTrue(result["pass"])
            self.assertTrue((dest/"review.png").exists())
            self.assertTrue(json.loads((dest/"quality.json").read_text())["requires_human_review"])
            with self.assertRaises(FileExistsError):stage([("neutral",im)],im,dest)
    def test_contact_sheet_native(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"sheet.png";contact_sheet([("one",face()),("two",face())],p)
            with Image.open(p) as im:self.assertEqual(im.size,(1024,286))
    def test_pose_axes(self):
        self.assertLessEqual(len(POSES),16)
        self.assertTrue(any(v[2]!=0 for v in POSES))
        self.assertTrue(any(v[3]!=0 for v in POSES))
        self.assertTrue(any(v[4]>0 for v in POSES))

class AdaptivePoseTests(unittest.TestCase):
    def test_retries_smaller_nonzero_angle_without_lowering_gate(self):
        source=face(); blurred=source.filter(ImageFilter.GaussianBlur(7))
        calls=[]
        def render(yaw,pitch,roll,mouth):
            calls.append(yaw)
            return blurred if yaw > 2 else source
        frame,detail=choose_sharp_pose("yaw_right_4",(4,0,0),0,source,render)
        self.assertEqual(calls,[4,3,2])
        self.assertEqual(detail["actual_degrees"],[2,0,0])
        self.assertEqual(detail["scale"],0.5)
        self.assertTrue(inspect([("pose",frame)],source)["pass"])

    def test_never_accepts_blurry_pose_or_neutral_fallback(self):
        source=face();blurred=source.filter(ImageFilter.GaussianBlur(7))
        calls=[]
        def render(yaw,pitch,roll,mouth):
            calls.append(yaw)
            return blurred
        with self.assertRaisesRegex(ValueError,"no nonzero model pose"):
            choose_sharp_pose("yaw_right_4",(4,0,0),0,source,render)
        self.assertEqual(calls,[4,3,2,1])

    def test_mouth_expression_not_reduced_or_retried(self):
        source=face();calls=[]
        def render(yaw,pitch,roll,mouth):
            calls.append((yaw,pitch,roll,mouth))
            return source
        _,detail=choose_sharp_pose("mouth_open",(0,0,0),0.9,source,render)
        self.assertEqual(calls,[(0,0,0,0.9)])
        self.assertEqual(detail["scale"],1.0)
