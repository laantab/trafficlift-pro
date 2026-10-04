# Free narrated product video studio

Double-click **Start_Free_Video_Studio.bat** in the repository on Windows.
The first run installs a separate environment and downloads the free Kokoro voice and FFmpeg. Python 3.11 is required. It opens TrafficLift on localhost, automatically selecting the local backend. No pairing code, GPU, paid API, or subscription is used by the video renderer.

Find/select a product, open Create Product Video, confirm its photo and seller destination, optionally paste one seller-supported benefit, and click Render Video. Existing generated marketing copy is deliberately not treated as factual evidence. For research-article winners, replace the destination with the seller page. The script otherwise uses a conservative product-name/design showcase without inventing features.

Defaults: 15 seconds, Warm Studio, warm female voice. Choices: 15/30/60 seconds and Warm/Clean/Bold scenes. All use reveal → showcase → CTA. Longer options hold scenes longer; they do not automatically create additional benefits. This is a narrated photo compositor, not generative product motion.

Progress: Getting photo → Making video → Checking video. A complete decode, format, audio, length and per-frame text-bound checks must pass before Play/Download. Captions use approximate chunk timing within measured utterances. Past videos persist under Documents/TrafficLift-Free-Videos, named by product and date in the app. Cap: 100 records, single render at a time. Failed and interrupted jobs remain visible. Keep the studio window open. Do not retry a timed-out render before checking history. MP4s contain no clickable links: attach the provided seller destination when posting.

This change does not enable public cloud rendering. The routes require explicit TRAFFICLIFT_FREE_VIDEO=1, loopback access, a localhost Host and same-origin browser requests. Render's existing requirements and deployment cost are unchanged. Existing RTX and MiniMax paths remain available. A shared cloud service would need authenticated ownership, process-wide scheduling, retention and resource limits before enablement.

Linux developer setup: install requirements-video.txt and FFmpeg/FFprobe, set TRAFFICLIFT_FREE_VIDEO=1, TRAFFICLIFT_VOICE_MODEL and TRAFFICLIFT_VOICES to model paths, optionally TRAFFICLIFT_FONT, then bind uvicorn to 127.0.0.1:8000. Open /studio?api=http://127.0.0.1:8000. Video endpoints do not scrape or generate new campaigns, spend, or call any AI provider.

Model downloads: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0 (SHA-256 verified). Kokoro weights Apache-2.0; keep upstream license notices when redistributing. FFmpeg build license notices remain in the portable tools folder. Seller images and optional factual text require appropriate usage rights and human claim review before publishing. Technical PASS does not establish ad policy compliance or conversion performance.

Validation limitations: Windows installer and Windows font/subtitle rendering must be exercised on the actual PC. No social-platform upload or customer conversion claims are made.
