TRAFFICLIFT PRO — LOCAL RTX VIDEO BRIDGE

This connects the TrafficLift Pro Video button to the existing LTX install on this PC.
It does not reinstall Python, models, CUDA, or the NVIDIA driver.

START
1. Double-click Start_Local_Video_Bridge.bat.
2. Leave its black window open. It prints a one-time pairing code.
3. Open TrafficLift Pro, choose Video, select Local RTX 3060, and enter that code.
4. Choose the local duration and click Render Video.

The bridge binds only to this PC (127.0.0.1), accepts the TrafficLift Pro GitHub Pages
origin, requires the one-time pairing code, and runs one video job at a time. Chrome may
ask to allow TrafficLift Pro to access a local network device; allow it for the feature.

The local LTX model produces silent image-to-video. The initial conservative output is
384 x 672 at 24 fps. Local generation may take several minutes. If a long duration uses
too much video memory, choose a shorter duration. MiniMax remains an optional provider.

If a job fails, its full render output is saved under:
%USERPROFILE%\Documents\TrafficLift-Local-Video\bridge_jobs\<job-id>\render.log
