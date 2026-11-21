# Neural Avatar Lab

Avatar Lab is an authenticated AX page at `/avatar-lab`. Open the Avatar Lab
navigation link in the workspace, or use the direct URL. If sign-in is required,
AX preserves this exact destination and returns to the lab after authentication.

## Try the prototype

1. Select **Start neural preview**. The first load transfers approximately
   100 MB of model and runtime files from AX. Subsequent requests can use the
   browser HTTP cache.
2. The first generated frame replaces the source photograph only after real
   inference succeeds. **Small turn**, **Mouth open**, **Soft smile**, and **Blink**
   expose the model's pose and expression controls.
3. **Play motion** advances through small pose steps and plays intermediate
   frames between completed neural renders. Slower devices show slower movement.
   **Pause motion** stops advancing the pose; an already-running render and
   transition can finish.
4. **Choose a photo** accepts JPEG, PNG, and WebP files up to 15 MB. Use one
   front-facing face with even lighting and a closed mouth. Adjust the square
   crop and select **Apply crop**. The image is decoded, cropped, and processed
   in the browser. Its pixels are not uploaded or stored by AX.

The included fictional portrait was generated for this project using OpenAI's
built-in image generation tool. The source asset is
`services/ax/avatar-assets/sample-portrait.png`. Its generation prompt requested
a centered, upright, neutral adult woman with medium brown skin, dark eyes,
shoulder-length dark hair, a plain charcoal shirt, natural skin texture, soft
studio lighting and a light-gray background, with no text or graphics.

## Technique and limits

This is a **LivePortrait-family neural portrait renderer**, using LP-Distilled
0.1 and a LivePortrait appearance encoder. It demonstrates the learned
portrait-synthesis family discussed alongside Apple's face-video research. It
is not Apple's model or an implementation of Apple's Audio2Mesh paper.

The source crop is encoded once to appearance features. The fused model takes
those features, the original crop, three pose controls, and thirty expression
controls, then synthesizes a new RGB frame. It does not rotate a sphere mesh or
play prerecorded result frames. No face detector or landmark weights are used;
the user aligns a square crop manually.

Output is 256 × 256 pixels. Modest pose changes preserve photographic detail
better than extreme expressions or large turns. This experimental model can
still distort eyes, teeth, hair, and face boundaries. It does not reconstruct an
arbitrary-view 3D head or provide audio-synchronized lip animation.

Inference runs in a dedicated browser worker. WebGPU is attempted first; a
failed GPU initialization or run switches to the same model using WebAssembly
on the CPU. The page reports the actual provider and measured time per frame.
CPU preview can take several seconds per frame and is not a realtime claim.
Only one inference runs at a time, and control changes are coalesced. The
512 MB AX server only serves files; it does not run these neural models.

### Smooth motion playback

The motion phase advances in bounded steps after accepted renders, rather than
sampling a new pose from wall-clock time after a slow model call. A separate
animation clock presents intermediate images between real neural endpoints.
Playback uses the recent inference duration, with a finite upper limit, to
spread a small movement across the next render interval. CPU fallback can
therefore show smooth, slow movement; unusually slow rendering can still leave
the last completed image on screen.

`portrait-motion.js` estimates small forward and backward movements on reduced
images, then warps and blends the two real neural frames. It does not download
another model, request more inference for every display frame, or predict past
the newest available neural frame. Both endpoints remain exact model output.
Low-confidence regions use a conservative blend; occlusions, teeth, hair and
large expression changes can still show artifacts. This is an interpolation
prototype, not a claim of additional neural frames or photorealistic video.

The compositor retains one pair of neural images, a reusable display buffer,
and small motion fields. The UI may hold at most one next neural image while a
transition finishes. Source changes, crop application, errors and logout clear
the interpolation state; logout also releases the inference worker immediately.
This bounds the extra playback memory but does not move the existing model
weights or inference to Render.

## Reproducible assets and security

`services/ax/portrait-assets.json` pins immutable model revisions, exact runtime
version 1.20.1, byte sizes, and SHA-256 hashes. The AX Docker build runs
`scripts/fetch_portrait_assets.py`, which downloads public files with bounded
retries and verifies their hashes before atomic installation. Startup does not
download models. Weights and runtime binaries are ignored by Git; the final
tested AX image includes them. The reference photo and full notices are in Git.

All browser code, models and sample images are served from AX. The scoped CSP
allows local scripts, workers and WebAssembly. Existing signed, Secure,
HttpOnly, SameSite=Strict login cookies remain required for the lab document.
Only packaged public model/runtime/reference files are served by
`/avatar-assets`; the raw lab document is blocked from the public static route.
No new inference provider, API credential, Render instance, or paid service is
required. See `/avatar-assets/THIRD_PARTY_NOTICES.txt` for upstream licensing.

## Validation and release

Local tests cover authenticated routes, the strict return path, static-file
boundaries, download integrity, control bounds, model-worker serialization,
GPU-to-CPU fallback, tensor disposal, and UI lifecycle behavior. Synthetic
runtime tests do not establish model quality or live deployment.

The existing container smoke gate additionally loads the **real bundled model
in Chromium** and renders neutral, mouth, turn, and smile frames from the
fictional sample. It checks nonblank pixels, control-dependent pixel changes,
same-origin GET-only requests, and desktop/mobile layout. The motion gate also
checks changing intermediate canvas pixels while the neural-frame count stays
fixed, exact endpoint pixels, bounded workers/render requests, crop reset and
immediate logout disposal. It saves the earlier, intermediate and later images
from a real model transition for visual review. It writes
`avatar-neural-acceptance.json` and screenshots to the normal CI evidence
artifact. This is a disposable container/browser test, separate from production
browser verification and James's visual acceptance.

Release through the existing source PR, required checks, tested-image CD,
separate AX digest promotion, and reviewed existing Render Blueprint plan.
Keep Blueprint auto-sync off and preserve the other three service image pins.

## Primary references

- [LP-Distilled model](https://huggingface.co/slperez/LP-Distilled0.1-ONNX)
- [Browser control mappings](https://github.com/stephenperez/lp-distilled-js)
- [LivePortrait](https://github.com/KwaiVGI/LivePortrait)
- [FasterLivePortrait ONNX conversion](https://github.com/warmshao/FasterLivePortrait)
- [Apple: Neural Face Video Compression using Multiple Views](https://machinelearning.apple.com/research/neural-face-video)
- [ONNX Runtime Web](https://onnxruntime.ai/docs/get-started/with-javascript/web.html)
