import asyncio
import time

from core.contracts import ComputerPoint, Settings, LongClench, DoubleBlink
from tests.test_computer import make, settle


def layout(c):
    c._event({"kind":"layout", "seq":c.seq, "tiles":[dict(id=key,label=label,left=0,top=i/len(c.items()),
                right=1,bottom=(i+1)/len(c.items())) for i,(key,label) in enumerate(c.items())]})


def point(c, index, source="gaze", **kwargs):
    return ComputerPoint(seq=c.seq, tile=index, source=source, found=True, t=time.time(), **kwargs)


def test_gaze_selects_browser_menu_and_stale_points_are_ignored():
    async def run():
        s, clock, _ = make()
        await settle()
        c=s.computer
        s.handle(Settings(pointing_mode="gaze",scan_ms=500))
        layout(c)
        clock.advance(2)
        assert c.selection.index == 0 and not c.scan.running
        stale=point(c,0)
        s.handle(point(c,1))
        assert c.selection.index==1 and c.pointer.source=="gaze"
        c.pick()
        assert c.selection.level=="menu"
        s.handle(stale)
        assert c.selection.index==0
        layout(c)
        s.handle(point(c,4))
        c.pick()
        assert not c.active and s.current_view().path==[]
        await c.aclose();s.stop()
    asyncio.run(run())


def test_auto_falls_back_when_tracker_stops_and_help_keeps_selection():
    async def run():
        s,clock,_=make()
        await settle()
        c=s.computer
        s.handle(Settings(pointing_mode="auto",scan_ms=500))
        layout(c)
        s.handle(point(c,1))
        assert c.pointer.source=="gaze" and c.selection.index==1
        s.handle(LongClench(t=0,duration=3))
        before=c.selection.index
        s.handle(point(c,0))
        assert c.selection.index==before and not c.pointer_running
        s.handle(DoubleBlink(t=0))
        layout(c)
        s.handle(point(c,1))
        clock.advance(4.1)
        assert c.pointer.source=="scan"
        clock.advance(.5)
        assert c.selection.index==0
        await c.aclose();s.stop()
    asyncio.run(run())


def test_dev_toggle_settings_and_webcam_lookback():
    async def run():
        s,clock,_=make()
        await settle()
        c=s.computer
        c._event({"kind":"input","event":"DEV_TOGGLE"})
        assert c.dev_open and not c.pointer_running
        c._event({"kind":"settings","patch":{"pointing_mode":"webcam","scan_ms":600}})
        assert s.pointing_mode=="webcam" and c.mode=="webcam"
        c.back()
        assert not c.dev_open
        layout(c)
        s.handle(point(c,1,"webcam"))
        clock.advance(.3)
        s.handle(point(c,0,"webcam"))
        c.pick()
        assert c.selection.level=="menu"  # selected where the head was before the clench nudge
        layout(c)
        s.handle(point(c,4,"webcam"))
        clock.advance(.4)
        c._event({"kind":"input","event":"DEV_TOGGLE"})
        c._event({"kind":"input","event":"CLENCH"})
        assert not c.active  # caregiver Clench also works after the panel pauses pointing
        await c.aclose();s.stop()
    asyncio.run(run())


def test_auto_webcam_lookback_does_not_use_scan_history():
    async def run():
        s,clock,_=make()
        await settle()
        c=s.computer
        s.handle(Settings(pointing_mode="auto",scan_ms=1000))
        layout(c)
        c._tick(0)
        clock.advance(.1)
        s.handle(point(c,1,"webcam"))
        clock.advance(.3)
        c.pick()
        assert c.selection.level=="menu"
        await c.aclose();s.stop()
    asyncio.run(run())


def test_dwell_is_bound_to_layout_and_paused_during_calibration_and_help():
    async def run():
        s,clock,_=make()
        await settle()
        c=s.computer
        s.handle(Settings(pointing_mode="gaze",scan_ms=500))
        layout(c)
        clock.advance(.4)
        stale=point(c,1,pick=True)
        s.handle(stale)
        assert c.selection.level=="menu"
        layout(c)
        clock.advance(.4)
        s.handle(stale)
        assert c.selection.level=="menu" and not c.busy
        c._event({"kind":"control","control":{"action":"calibrate"}})
        s.handle(point(c,4,pick=True))
        assert c.active and c.view().paused
        s.handle(LongClench(t=0,duration=3))
        assert c.help==5
        c._event({"kind":"control","control":{"action":"calibration_done"}})
        s.handle(DoubleBlink(t=0))
        assert c.active and c.help is None and not c.calibrating
        await c.aclose();s.stop()
    asyncio.run(run())
