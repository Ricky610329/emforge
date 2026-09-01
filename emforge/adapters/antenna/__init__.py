"""emforge.adapters.antenna — 毫米波像素化天線／濾波器域（舊 Antenna repo 的 HFSS 模擬器）。

  _bind.py     找舊 repo（EMFORGE_ANTENNA_REPO）、lazy import、清楚的失敗訊息、antenna git sha
  measure.py   凍結 targets ＋ vendored numpy 版兩把尺（worst_margin／worst_margin_dual／dual_energy_max）＋ parity 測試鉤子
  sim.py       DualPortSim／SinglePortRadSim：把舊 open/start/__call__/end/quit 包成 open/simulate/kill/close
  profiles.py  register_all()：measures、specs（dual_v1／dual_v2／single_v1）、profiles（dual_p01_db075、single_db100、退役數個）

import 這個套件**不會** import torch／antenna（子行程與 runtime 都很輕）；只有 sim 真的建構時才綁舊 repo。
"""
