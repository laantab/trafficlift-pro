import argparse, sys, time
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repo',required=True); p.add_argument('--image',required=True); p.add_argument('--prompt',required=True); p.add_argument('--output',required=True)
    p.add_argument('--width',type=int,default=384); p.add_argument('--height',type=int,default=672); p.add_argument('--frames',type=int,default=121); p.add_argument('--fps',type=int,default=30); p.add_argument('--seed',type=int,default=171198)
    a=p.parse_args(); repo=Path(a.repo).resolve(); image=Path(a.image).resolve(); output=Path(a.output).resolve(); config=repo/'configs'/'trafficlift-rtx3060-2b.yaml'
    sys.path.insert(0,str(repo))
    from ltx_video.inference import infer, InferenceConfig
    import torch
    if not torch.cuda.is_available(): raise SystemExit('CUDA unavailable')
    output.mkdir(parents=True,exist_ok=True); before={x.resolve() for x in output.rglob('*.mp4')}
    cfg=InferenceConfig(prompt=a.prompt,output_path=output,pipeline_config=str(config),seed=a.seed,height=a.height,width=a.width,num_frames=a.frames,frame_rate=a.fps,offload_to_cpu=False,conditioning_media_paths=[str(image)],conditioning_strengths=[1.0],conditioning_start_frames=[0])
    t=time.time(); infer(cfg); elapsed=time.time()-t
    after=[x.resolve() for x in output.rglob('*.mp4')]; new=[x for x in after if x not in before]; cand=max(new or after,key=lambda x:x.stat().st_mtime) if after else None
    print(f'ELAPSED_SECONDS={elapsed:.1f}')
    if not cand: raise SystemExit('No MP4 found after inference')
    print('VIDEO_OUTPUT='+str(cand)); print('LOCAL_VIDEO_OK')
if __name__=='__main__': main()
