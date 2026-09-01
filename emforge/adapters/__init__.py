"""emforge.adapters — 領域轉接層。核心零領域依賴；每個領域一個子套件，把外部模擬器包成 Simulator 協定、
把量測尺 vendor 成凍結的 numpy 版、把 profile／spec 用 `register_all()` 交給使用者的 `<root>/registry.py` 選用。"""
