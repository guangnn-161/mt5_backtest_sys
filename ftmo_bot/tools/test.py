import MetaTrader5 as mt5

mt5.initialize()
# Tìm tất cả các mã có chứa chữ "XAU" hoặc "GOLD"
symbols = mt5.symbols_get()
gold_symbols = [s.name for s in symbols if "XAU" in s.name or "GOLD" in s.name]

print("Các mã Vàng có trên sàn của bạn:", gold_symbols)
mt5.shutdown()
