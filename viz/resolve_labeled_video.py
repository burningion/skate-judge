"""Build and render a layered labeled-attempt timeline with Resolve's native API.

Use ResolvePython, after build_labeled_video.py. Commands: build, render, status,
stills, export. The build creates a dedicated project and never replaces a timeline.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import DaVinciResolveScript as dvr

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "sessions/edits/labeled-imu-five"
PROJECT = "Labeled IMU - Two Landed Two Falls One Bail"
TIMELINE = "Five examples - six axes - editable"


def count_word():
    count=len(json.loads((OUTPUT/"manifest.json").read_text())["clips"])
    return {5:"Five",7:"Seven"}.get(count,str(count))


def write(name, value):
    (OUTPUT/name).write_text(json.dumps(value,indent=2)+"\n")


def connect():
    r=dvr.scriptapp("Resolve")
    assert r, "Start Resolve and enable local scripting."
    return r,r.GetProjectManager()


def current():
    r,m=connect()
    p=m.GetCurrentProject()
    assert p and p.GetName()==PROJECT, "Open the dedicated Labeled IMU project first."
    matches=[p.GetTimelineByIndex(i) for i in range(1,p.GetTimelineCount()+1) if p.GetTimelineByIndex(i).GetName()==TIMELINE]
    assert len(matches)==1, "Expected exactly one matching template timeline."
    t=matches[0]
    if p.GetCurrentTimeline().GetUniqueId()!=t.GetUniqueId():
        assert p.SetCurrentTimeline(t)
    return r,m,p,t


def configure(p,t):
    assert p.SetCurrentTimeline(t)
    assert t.SetStartTimecode("00:00:00:00")
    assert t.SetSettings({"useCustomSettings":"1"})
    for k,v in {"timelineResolutionWidth":"2160","timelineResolutionHeight":"3840",
                "timelineFrameRate":"59.94","timelineDropFrameTimecode":"0",
                "timelineInputResMismatchBehavior":"scaleToFit","timelineOutputResMatchTimelineRes":"1"}.items():
        existing=t.GetSettings().get(k)
        if str(existing)==str(v):
            continue
        assert t.SetSettings({k:v}), (k,v)


def append(pool, media, track, record, first, end, kind=1):
    added=pool.AppendToTimeline([dict(mediaPoolItem=media,startFrame=first,endFrame=end,mediaType=kind,trackIndex=track,recordFrame=record)])
    assert added and len(added)==1
    return added[0]


def place(resolve,item,x,y,w,h):
    assert item.SetProperties({"Scaling":resolve.SCALE_STRETCH,"ZoomGang":False})
    assert item.SetProperties({"ZoomX":w/2160,"ZoomY":h/3840,"Pan":x+w/2-1080,"Tilt":1920-y-h/2})


def outcome_title(clip):
    trick=clip["label"]["trick"].upper()
    outcome=clip["label"]["outcome"]
    return f"{trick} FALL" if outcome=="fall" else f"{ {'make':'LANDED','bail':'BAILED'}[outcome]} {trick}"


def clean_presentation(comp,clip,manifest):
    """Keep the heading, impact annotations, and playheads editable in Fusion."""
    if manifest.get("presentation")!="clean-impact":
        return
    W,H=manifest["width"],manifest["height"]
    color={"make":(.64,.92,.65),"fall":(1,.57,.47),"bail":(1,.79,.40)}[clip["label"]["outcome"]]
    comp.StartUndo("Simplify titles and label board impact")
    try:
        for name in ["Outcome_and_Number","Data_Description","Source_Label"]:
            comp.FindTool(name).SetInput("StyledText","")
        title=comp.FindTool("Trick_Name")
        for key,value in {"StyledText":outcome_title(clip),"Size":manifest.get("title_font_px",168)/W,
                          "Center":{1:.5,2:1-230/H},"Red1":color[0],"Green1":color[1],"Blue1":color[2]}.items():
            title.SetInput(key,value)
        def node(kind,name):
            tool=comp.FindTool(name)
            if not tool:
                tool=comp.AddTool(kind)
                assert tool
                tool.SetAttrs({"TOOLS_Name":name})
            return tool
        base=comp.FindTool("Merge_Source_Label")
        def merge(foreground,name):
            nonlocal base
            tool=node("Merge",name)
            tool.ConnectInput("Background",base)
            tool.ConnectInput("Foreground",foreground)
            base=tool
        lo,hi=manifest["graph_domain"]
        left,top,right,bottom=manifest["plot_box"]
        amber=(1.,209/255,102/255)
        for panel in clip["panels"]:
            name=panel["sensor"]+"_"+panel["axis"]
            x=panel["x"]+left+(clip["board_impact"]["seconds_from_pop"]-lo)/(hi-lo)*(right-left)
            mask=node("RectangleMask","Impact_"+name)
            for key,value in {"Width":6/W,"Height":(bottom-top)/H,
                              "Center":{1:x/W,2:1-(panel["y"]+(top+bottom)/2)/H}}.items():
                mask.SetInput(key,value)
            line=node("Background","Impact_Line_"+name)
            for key,value in {"TopLeftRed":amber[0],"TopLeftGreen":amber[1],"TopLeftBlue":amber[2],
                              "TopLeftAlpha":1.,"Width":W,"Height":H}.items():
                line.SetInput(key,value)
            line.ConnectInput("EffectMask",mask)
            merge(line,"Merge_Impact_"+name)
            label=node("TextPlus","Impact_Label_"+name)
            for key,value in {"StyledText":"BOARD IMPACT","Font":"Arial","Style":"Bold","Size":54/W,
                              "Center":{1:x/W,2:1-(panel["y"]+30)/H},
                              "Red1":amber[0],"Green1":amber[1],"Blue1":amber[2]}.items():
                label.SetInput(key,value)
            merge(label,"Merge_Impact_Label_"+name)
        first=clip["panels"][0]
        comp.FindTool("Merge_Cursor_"+first["sensor"]+"_"+first["axis"]).ConnectInput("Background",base)
    finally:
        comp.EndUndo(True)


def fusion_overlay(item, clip, manifest):
    comp=item.AddFusionComp()
    assert comp
    comp.StartUndo("Build editable titles and playheads")
    try:
        output=comp.FindTool("MediaOut1")
        base=comp.FindTool("MediaIn1")
        assert output and base
        W,H=manifest["width"],manifest["height"]
        def add(tool,name):
            node=comp.AddTool(tool)
            assert node
            node.SetAttrs({"TOOLS_Name":name})
            return node
        def merge(foreground,name):
            nonlocal base
            node=add("Merge",name)
            node.ConnectInput("Background",base)
            node.ConnectInput("Foreground",foreground)
            base=node
        def text(name,value,x,y,size,color=(.94,.97,1)):
            tool=add("TextPlus",name)
            for key,value_ in {"StyledText":value,"Font":"Arial","Style":"Bold","Size":size/W,
                               "Center":{1:x/W,2:1-y/H},"Red1":color[0],"Green1":color[1],"Blue1":color[2]}.items():
                tool.SetInput(key,value_)
            merge(tool,"Merge_"+name)
            return tool
        outcome=clip["label"]["outcome"]
        shown={"make":"LANDED","fall":"FALL","bail":"BAIL"}[outcome]
        color={"make":(.64,.92,.65),"fall":(1,.57,.47),"bail":(1,.79,.40)}[outcome]
        text("Outcome_and_Number",f"{shown}   /   {clip['number']:02d} OF {len(manifest['clips']):02d}",1080,120,76,color)
        text("Trick_Name",clip["label"]["trick"].upper(),1080,275,132)
        headings={panel["sensor"]:panel for panel in clip["panels"]}
        ap,gp=headings["acceleration"],headings["gyro"]
        text("Acceleration_Heading","ACCELERATION  (g)",ap.get("heading_x",560),ap.get("heading_y",1860),72 if manifest.get("layout")=="overlaid" else 64)
        text("Gyro_Heading","GYROSCOPE  (rad/s)",gp.get("heading_x",1600),gp.get("heading_y",1860),72 if manifest.get("layout")=="overlaid" else 64)
        text("Time_Axis_Title","TIME FROM POP  (s)",1080,3600,58)
        text("Data_Description","RAW SENSOR AXES  /  X · Y · Z",1080,3700,47,(.71,.79,.86))
        text("Source_Label",f"{clip['session']}   ·   {clip['label']['id'][:8]}   ·   REAL TIME",1080,3780,39,(.71,.79,.86))
        start=float(comp.GetAttrs().get("COMPN_GlobalStart",0))
        lo,hi=manifest["graph_domain"]
        left,top,right,bottom=manifest["plot_box"]
        for panel in clip["panels"]:
            name=panel["sensor"]+"_"+panel["axis"]
            mask=add("RectangleMask","Playhead_"+name)
            mask.SetInput("Width",(8 if manifest.get("layout")=="overlaid" else 4)/W)
            mask.SetInput("Height",(bottom-top)/H)
            x0=(panel["x"]+left+(clip["camera_start_s"]-clip["pop_camera_s"]-lo)/(hi-lo)*(right-left))/W
            step=(right-left)/(hi-lo)/manifest["fps"]/W
            center_y=1-(panel["y"]+(top+bottom)/2)/H
            mask.Center.SetExpression(f"Point({x0:.12f} + (time - {start:g}) * {step:.12f}, {center_y:.12f})")
            line=add("Background","Cursor_"+name)
            for key,value in {"TopLeftRed":1.,"TopLeftGreen":1.,"TopLeftBlue":1.,"TopLeftAlpha":.9,"Width":W,"Height":H}.items():
                line.SetInput(key,value)
            line.ConnectInput("EffectMask",mask)
            merge(line,"Merge_Cursor_"+name)
        output.ConnectInput("Input",base)
    finally:
        comp.EndUndo(True)
    clean_presentation(comp,clip,manifest)
    # Explicitly invalidate the active output after constructing the node graph.
    output.ConnectInput("Input",comp.FindTool("Trick_Name"))
    output.ConnectInput("Input",base)
    return comp


def build():
    manifest=json.loads((OUTPUT/"manifest.json").read_text())
    assert not (OUTPUT/"resolve-build.json").exists(), "Completed template exists; do not overwrite."
    panels=manifest["clips"][0]["panels"]
    track_count=len(panels)+3
    r,m=connect()
    previous=m.GetCurrentProject()
    resuming=previous and previous.GetName()==PROJECT
    if not resuming:
        assert PROJECT not in (m.GetProjectsInCurrentFolder() or {}).values(), "Project exists; use it rather than overwriting."
    if previous and not resuming:
        assert not previous.IsRenderingInProgress()
        assert m.SaveProject()
        assert m.ExportProject(previous.GetName(),str(OUTPUT/"previous-project-backup.drp"))
        write("project-setup.json",dict(previous_project=previous.GetName()))
    p=previous if resuming else m.CreateProject(PROJECT)
    assert p
    if not resuming:
        assert p.SetSettings({"timelineFrameRate":"59.94","timelineResolutionWidth":"2160","timelineResolutionHeight":"3840"})
        assert p.SetSettings({"colorScienceMode":"davinciYRGB","colorSpaceTimeline":"Rec.709 Gamma 2.4"})
    pool=p.GetMediaPool()
    folder=next((f for f in pool.GetRootFolder().GetSubFolderList() if f.GetName()=="Five labeled examples - source and graph assets"),None)
    folder=folder or pool.AddSubFolder(pool.GetRootFolder(),"Five labeled examples - source and graph assets")
    assert folder and pool.SetCurrentFolder(folder)
    source_paths=list(dict.fromkeys(c["source"] for c in manifest["clips"]))
    paths=source_paths+[str(p) for p in sorted((OUTPUT/"assets").glob("*.mov"))]
    assert len(paths)==len(source_paths)+len(manifest["clips"])*len(panels)+2, "Conform all graphics before assembly."
    imported=[c for c in folder.GetClipList() if c.GetClipProperty("File Path") in paths]
    existing_paths={c.GetClipProperty("File Path") for c in imported}
    missing=[path for path in paths if path not in existing_paths]
    imported+=pool.ImportMedia(missing) if missing else []
    assert len(imported)==len(paths),(len(imported),len(paths))
    media={c.GetClipProperty("File Path"):c for c in imported}
    for c in imported:
        if c.GetClipProperty("File Path").endswith("transparent.mov"):
            assert c.SetClipProperty("Alpha mode","Straight")
    matches=[p.GetTimelineByIndex(i) for i in range(1,p.GetTimelineCount()+1) if p.GetTimelineByIndex(i).GetName()==TIMELINE]
    assert len(matches)<=1
    if matches:
        t=matches[0]
    elif manifest.get("base_timeline"):
        bases=[p.GetTimelineByIndex(i) for i in range(1,p.GetTimelineCount()+1) if p.GetTimelineByIndex(i).GetName()==manifest["base_timeline"]]
        assert len(bases)==1
        t=bases[0].DuplicateTimeline(TIMELINE)
        assert t
    else:
        t=pool.CreateEmptyTimeline(TIMELINE)
    configure(p,t)
    while t.GetTrackCount("video")<track_count:
        assert t.AddTrack("video")
    names=["Background","Original footage - reframe here"]+[
        {"acceleration":"Acceleration","gyro":"Gyroscope"}[panel["sensor"]]+" "+panel["axis"] for panel in panels
    ]+["Editable bold titles and synchronized playheads"]
    for index,name in enumerate(names,1):
        assert t.SetTrackName("video",index,name)
    assert t.SetTrackName("audio",1,"Original camera audio")
    assert r.OpenPage("edit")
    placements=[]
    for clip in manifest["clips"]:
        record,count=clip["record_frame"],clip["timeline_frames"]
        existing={i:[c for c in t.GetItemListInTrack("video",i) or [] if c.GetStart()==record] for i in range(1,track_count+1)}
        if any(existing.values()):
            assert all(len(items)==1 and items[0].GetDuration()==count for items in existing.values()), "Incomplete clip requires inspection."
            video,overlay=existing[2][0],existing[track_count][0]
            assert video.GetMediaPoolItem().GetClipProperty("File Path")==clip["source"]
            assert abs(video.GetLeftOffset(True)*2-clip["source_first_frame"])<1e-5
            assert overlay.GetFusionCompCount()==1
            comp=overlay.GetFusionCompByIndex(1)
            shown={"make":"LANDED","fall":"FALL","bail":"BAIL"}[clip["label"]["outcome"]]
            comp.FindTool("Outcome_and_Number").SetInput("StyledText",f"{shown}   /   {clip['number']:02d} OF {len(manifest['clips']):02d}")
            clean_presentation(comp,clip,manifest)
            final_panel=clip["panels"][-1]
            final_merge="Merge_Cursor_"+final_panel["sensor"]+"_"+final_panel["axis"]
            comp.FindTool("MediaOut1").ConnectInput("Input",comp.FindTool("Trick_Name"))
            comp.FindTool("MediaOut1").ConnectInput("Input",comp.FindTool(final_merge))
        else:
            bg=append(pool,media[str(OUTPUT/"assets/background.mov")],1,record,0,count)
            assert bg.GetDuration()==count
            source=media[clip["source"]]
            video=append(pool,source,2,record,clip["source_first_frame"],clip["source_end_frame_exclusive"])
            assert video.GetDuration()==count,(video.GetDuration(),count)
            # GetSourceStartFrame can floor an exact boundary one frame low at NTSC rates.
            assert abs(video.GetSourceStartFrame()-clip["source_first_frame"])<=1
            assert abs(video.GetLeftOffset(True)*2-clip["source_first_frame"])<1e-5
            assert video.SetName(f"{clip['number']:02d} {clip['label']['trick']} - {clip['label']['outcome']}")
            # Fit the full camera frame, leaving room for the six separate graph layers.
            place(r,video,0,460,2160,1215)
            audio=append(pool,source,1,record,clip["source_first_frame"],clip["source_end_frame_exclusive"],2)
            assert audio.GetDuration()==count
            assert t.SetClipsLinked([video,audio],True)
            for panel_index,panel in enumerate(clip["panels"]):
                item=append(pool,media[str(Path(panel["file"]).with_suffix(".mov"))],panel_index+3,record,0,count)
                assert item.GetDuration()==count
                place(r,item,panel["x"],panel["y"],panel["width"],panel["height"])
            overlay=append(pool,media[str(OUTPUT/"assets/transparent.mov")],track_count,record,0,count)
            assert overlay.GetDuration()==count
            overlay.SetName(f"{clip['number']:02d} EDIT TITLES AND CURSORS IN FUSION")
            comp=fusion_overlay(overlay,clip,manifest)
        assert overlay.ExportFusionComp(str(OUTPUT/"template"/f"{clip['number']:02d}-titles-and-cursors.comp"),1)
        pop_frame=record+round((clip["pop_camera_s"]-clip["camera_start_s"])*manifest["fps"])
        if pop_frame not in t.GetMarkers():
            assert t.AddMarker(pop_frame,"Green" if clip["label"]["outcome"]=="make" else "Red",
                               f"{clip['number']:02d} POP - {clip['label']['trick']}",clip["label"]["id"],1)
        if manifest.get("presentation")=="clean-impact":
            impact_frame=record+round((clip["board_impact"]["camera_s"]-clip["camera_start_s"])*manifest["fps"])
            if impact_frame not in t.GetMarkers():
                assert t.AddMarker(impact_frame,"Yellow","BOARD IMPACT",clip["board_impact"]["source"],1)
        placements.append(dict(number=clip["number"],start=int(video.GetStart()),duration=int(video.GetDuration()),
                               source_start_frame=video.GetSourceStartFrame(),source_end_frame=video.GetSourceEndFrame(),
                               fusion_global_start=comp.GetAttrs().get("COMPN_GlobalStart"),fusion_global_end=comp.GetAttrs().get("COMPN_GlobalEnd")))
        print("BUILT",clip["number"],clip["label"]["trick"],clip["label"]["outcome"],flush=True)
    assert t.GetEndFrame()==manifest["total_frames"]
    assert m.SaveProject()
    setup=json.loads((OUTPUT/"project-setup.json").read_text()) if (OUTPUT/"project-setup.json").exists() else {}
    write("resolve-build.json",dict(project=PROJECT,timeline=TIMELINE,placements=placements,previous_project=setup.get("previous_project")))
    export()
    assert t.SetCurrentTimecode("00:00:01:15")
    print("BUILT AND SAVED",PROJECT,flush=True)


def export():
    r,m,p,t=current()
    assert m.SaveProject()
    assert t.Export(str(OUTPUT/f"{count_word()}-Examples-Editable.drt"),r.EXPORT_DRT,r.EXPORT_NONE)
    assert m.ExportProject(p.GetName(),str(OUTPUT/"Labeled-IMU-Editable.drp"))
    print("EXPORTED editable project and timeline",flush=True)


def refresh():
    """Update native typography and invalidate the render output without a comp lock."""
    r,m,p,t=current()
    manifest=json.loads((OUTPUT/"manifest.json").read_text())
    panels=manifest["clips"][0]["panels"]
    last=panels[-1]
    final_merge="Merge_Cursor_"+last["sensor"]+"_"+last["axis"]
    sizes={"Outcome_and_Number":76,"Trick_Name":132,"Acceleration_Heading":64,"Gyro_Heading":64,
           "Time_Axis_Title":58,"Data_Description":47,"Source_Label":39}
    if manifest.get("layout")=="overlaid":
        sizes.update(Acceleration_Heading=72,Gyro_Heading=72)
    for i,item in enumerate(t.GetItemListInTrack("video",len(panels)+3),1):
        comp=item.GetFusionCompByIndex(1)
        shown={"make":"LANDED","fall":"FALL","bail":"BAIL"}[manifest["clips"][i-1]["label"]["outcome"]]
        comp.FindTool("Outcome_and_Number").SetInput("StyledText",f"{shown}   /   {i:02d} OF {len(manifest['clips']):02d}")
        for name,size in sizes.items():
            comp.FindTool(name).SetInput("Size",size/2160)
        clean_presentation(comp,manifest["clips"][i-1],manifest)
        out=comp.FindTool("MediaOut1")
        assert out.ConnectInput("Input",comp.FindTool("Trick_Name"))
        assert out.ConnectInput("Input",comp.FindTool(final_merge))
        assert item.ExportFusionComp(str(OUTPUT/"template"/f"{i:02d}-titles-and-cursors.comp"),1)
    export()


def audit():
    r,m,p,t=current()
    manifest=json.loads((OUTPUT/"manifest.json").read_text())
    panels=manifest["clips"][0]["panels"]
    track_count=len(panels)+3
    count=len(manifest["clips"])
    clean=manifest.get("presentation")=="clean-impact"
    final_merge="Merge_Cursor_"+panels[-1]["sensor"]+"_"+panels[-1]["axis"]
    assert float(t.GetSettings()["timelineFrameRate"])==59.94
    assert t.GetTrackCount("video")==track_count and t.GetEndFrame()==manifest["total_frames"]
    for track in range(1,track_count+1):
        clips=t.GetItemListInTrack("video",track)
        assert len(clips)==count
        for i,item in enumerate(clips):
            expected=manifest["clips"][i]
            assert item.GetStart()==expected["record_frame"] and item.GetDuration()==expected["timeline_frames"]
            if track==2:
                assert abs(item.GetLeftOffset(True)*2-manifest["clips"][i]["source_first_frame"])<1e-5
            if track==track_count:
                assert item.GetFusionCompCount()==1
                comp=item.GetFusionCompByIndex(1)
                assert len(comp.GetToolList(False,"TextPlus"))==7+(len(panels) if clean else 0)
                assert len(comp.GetToolList(False,"RectangleMask"))==len(panels)*(2 if clean else 1)
                assert comp.FindTool("Trick_Name").GetInput("Font")=="Arial"
                assert comp.FindTool("Trick_Name").GetInput("Style")=="Bold"
                shown={"make":"LANDED","fall":"FALL","bail":"BAIL"}[expected["label"]["outcome"]]
                if clean:
                    for name in ["Outcome_and_Number","Data_Description","Source_Label"]:
                        assert comp.FindTool(name).GetInput("StyledText")==""
                    assert comp.FindTool("Trick_Name").GetInput("StyledText")==outcome_title(expected)
                    assert abs(comp.FindTool("Trick_Name").GetInput("Size")-manifest["title_font_px"]/2160)<1e-8
                    lo,hi=manifest["graph_domain"]
                    left,top,right,bottom=manifest["plot_box"]
                    for panel in panels:
                        name=panel["sensor"]+"_"+panel["axis"]
                        x=(panel["x"]+left+(expected["board_impact"]["seconds_from_pop"]-lo)/(hi-lo)*(right-left))/2160
                        assert comp.FindTool("Impact_Label_"+name).GetInput("StyledText")=="BOARD IMPACT"
                        assert abs(comp.FindTool("Impact_"+name).GetInput("Center")[1]-x)<1e-8
                    impact_frame=expected["record_frame"]+round((expected["board_impact"]["camera_s"]-expected["camera_start_s"])*manifest["fps"])
                    assert t.GetMarkers()[impact_frame]["name"]=="BOARD IMPACT"
                else:
                    assert comp.FindTool("Outcome_and_Number").GetInput("StyledText")==f"{shown}   /   {i+1:02d} OF {count:02d}"
                assert comp.FindTool("MediaOut1").Input.GetConnectedOutput().GetTool().GetAttrs()["TOOLS_Name"]==final_merge
    assert len(t.GetItemListInTrack("audio",1))==count
    assert r.OpenPage("edit")
    assert t.SetCurrentTimecode("00:00:01:20")
    write("resolve-audit.json",dict(passed=True,video_tracks=track_count,audio_tracks=1,video_items=track_count*count,native_fusion_compositions=count,
                                    native_text_nodes=(7+(len(panels) if clean else 0))*count,native_playheads=len(panels)*count,
                                    native_board_impact_markers=len(panels)*count if clean else 0,
                                    combined_outcome_titles=clean,counters_and_footers_removed=clean,
                                    frames=manifest["total_frames"],timeline_fps=t.GetSettings()["timelineFrameRate"]))
    export()
    print("NATIVE PROJECT AUDIT PASSED",flush=True)


def render():
    r,m,p,t=current()
    assert not p.IsRenderingInProgress()
    assert p.SetCurrentRenderMode(1)
    assert p.SetCurrentRenderFormatAndCodec("mov","ProRes422HQ")
    assert p.SetRenderSettings({"SelectAllFrames":True,"TargetDir":str(OUTPUT),"CustomName":f"Labeled-IMU-{count_word()}-Examples_2160x3840_5994_ProResHQ",
                                "ExportVideo":True,"ExportAudio":True,"FormatWidth":2160,"FormatHeight":3840,"FrameRate":59.94,
                                "UseUniqueFilenames":False,"ColorSpaceTag":"Rec.709","GammaTag":"Gamma 2.4"})
    job=p.AddRenderJob()
    assert job
    write("render-job.json",dict(id=job))
    assert p.StartRendering([job])
    print("STARTED",job,flush=True)


def status():
    r,m,p,t=current()
    job=json.loads((OUTPUT/"render-job.json").read_text())["id"]
    print(json.dumps(dict(rendering=p.IsRenderingInProgress(),status=p.GetRenderJobStatus(job))),flush=True)


def proof():
    r,m,p,t=current()
    assert not p.IsRenderingInProgress()
    assert p.SetCurrentRenderMode(1)
    assert p.SetCurrentRenderFormatAndCodec("mov","ProRes422HQ")
    assert p.SetRenderSettings({"SelectAllFrames":False,"MarkIn":80,"MarkOut":80,"TargetDir":str(OUTPUT),"CustomName":"layout-proof",
                                "ExportVideo":True,"ExportAudio":False,"FormatWidth":2160,"FormatHeight":3840,"FrameRate":59.94,
                                "UseUniqueFilenames":False})
    job=p.AddRenderJob()
    assert job and p.StartRendering([job])
    deadline=time.monotonic()+40
    while p.IsRenderingInProgress() and time.monotonic()<deadline:
        time.sleep(1)
    print("PROOF",p.GetRenderJobStatus(job),flush=True)


def stills():
    r,m,p,t=current()
    for index in range(len(json.loads((OUTPUT/"manifest.json").read_text())["clips"])):
        frame=index*180+80
        tc=f"00:00:{frame//60:02d}:{frame%60:02d}"
        assert t.SetCurrentTimecode(tc)
        time.sleep(1)
        dest=OUTPUT/f"review-{index+1:02d}.png"
        if dest.exists():
            dest=OUTPUT/f"review-{index+1:02d}-{time.time_ns()}.png"
        for attempt in range(5):
            if p.ExportCurrentFrameAsStill(str(dest)):
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"Could not export review frame {index+1}; use the delivered movie for review.")
    assert t.SetCurrentTimecode("00:00:01:20")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["build","render","status","stills","export","proof","refresh","audit"])
    parser.add_argument("--output",type=Path,default=OUTPUT)
    parser.add_argument("--timeline")
    args=parser.parse_args()
    OUTPUT=args.output.resolve()
    manifest=json.loads((OUTPUT/"manifest.json").read_text())
    layout=manifest.get("layout","separate")
    default_timeline=f"{count_word()} examples - overlaid XYZ - editable" if layout=="overlaid" else TIMELINE
    if manifest.get("presentation")=="clean-impact":
        default_timeline=f"{count_word()} examples - clean labels and impacts - editable"
    TIMELINE=args.timeline or default_timeline
    globals()[args.command]()
