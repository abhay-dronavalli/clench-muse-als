"""Desktop control: eyes and jaw for all of Windows (docs/desktop-control.md).

  desktop/eyedid/   Eyedid Windows SDK through its C API (ctypes), run in a worker process
  desktop/agent/    the agent: overlay, snapping, clicks, keyboard stand-in, Core link

Pure modules (no Qt, no Windows calls) are imported by the tests; everything that touches Windows
or Qt is imported only by the running agent.
"""
