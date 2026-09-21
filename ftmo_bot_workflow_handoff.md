# FTMO MT5 Trading Bot — Tài liệu quy trình đầy đủ (Handoff Document)

> **Mục đích của tài liệu này:** Mô tả toàn bộ quy trình nghiên cứu, phát triển, kiểm định và vận hành một trading bot (Expert Advisor) trên MetaTrader 5, với mục tiêu vượt qua bài kiểm tra quỹ (Challenge) của FTMO.
>
> **Đối tượng đọc:** Một AI assistant khác hoặc một cộng tác viên kỹ thuật tiếp nhận dự án. Tài liệu được viết để người/AI đọc có thể nắm được bối cảnh, triết lý thiết kế, các quyết định kiến trúc, và có thể tiếp tục công việc mà không cần hỏi lại từ đầu.
>
> **Trạng thái dự án:** Giai đoạn khởi tạo. Chưa chọn chiến lược cụ thể. Đã thống nhất kiến trúc tổng thể, phương pháp kiểm định (Monte Carlo), và hệ thống ghi log nghiên cứu.
>
> **Ngày cập nhật:** 2026-09-17

---

## Mục lục

1. [Bối cảnh và mục tiêu](#1-bối-cảnh-và-mục-tiêu)
2. [Luật chơi FTMO — ràng buộc thiết kế](#2-luật-chơi-ftmo--ràng-buộc-thiết-kế)
3. [Triết lý thiết kế cốt lõi](#3-triết-lý-thiết-kế-cốt-lõi)
4. [Lựa chọn công nghệ: Python vs MQL5](#4-lựa-chọn-công-nghệ-python-vs-mql5)
5. [Kiến trúc thư mục dự án](#5-kiến-trúc-thư-mục-dự-án)
6. [Pipeline 6 giai đoạn](#6-pipeline-6-giai-đoạn)
7. [Risk Management Module — trái tim của hệ thống](#7-risk-management-module--trái-tim-của-hệ-thống)
8. [Monte Carlo — phương pháp kiểm định chính](#8-monte-carlo--phương-pháp-kiểm-định-chính)
9. [Hệ thống ghi log nghiên cứu hai tầng](#9-hệ-thống-ghi-log-nghiên-cứu-hai-tầng)
10. [Gate criteria — tiêu chí chuyển giai đoạn](#10-gate-criteria--tiêu-chí-chuyển-giai-đoạn)
11. [Vận hành và giám sát khi chạy live](#11-vận-hành-và-giám-sát-khi-chạy-live)
12. [Những cạm bẫy đã biết](#12-những-cạm-bẫy-đã-biết)
13. [Trạng thái hiện tại và bước tiếp theo](#13-trạng-thái-hiện-tại-và-bước-tiếp-theo)
14. [Phụ lục: Code tham chiếu](#14-phụ-lục-code-tham-chiếu)

---

## 1. Bối cảnh và mục tiêu

### 1.1. Mục tiêu dự án

Xây dựng một hệ thống giao dịch tự động chạy trên MetaTrader 5, có khả năng vượt qua FTMO Challenge — một bài kiểm tra do quỹ FTMO tổ chức, trong đó trader phải đạt mục tiêu lợi nhuận nhất định mà không vi phạm các giới hạn rủi ro nghiêm ngặt.

### 1.2. Định nghĩa "thành công" của dự án

Điểm quan trọng nhất cần hiểu: **mục tiêu không phải là tối đa hóa lợi nhuận, mà là tối đa hóa xác suất vượt qua bài kiểm tra.** Đây là hai bài toán khác nhau về bản chất:

| | Tối đa lợi nhuận | Tối đa xác suất pass |
|---|---|---|
| Hàm mục tiêu | Expected return | P(đạt target AND không vi phạm limit) |
| Thái độ với rủi ro | Chấp nhận drawdown sâu nếu kỳ vọng dương | Drawdown sâu = thất bại tuyệt đối |
| Kích thước lệnh | Càng lớn càng tốt nếu edge dương | Nhỏ, vì vi phạm limit là game over |
| Chỉ số đánh giá | Total return, Sharpe ratio | P(pass) qua mô phỏng Monte Carlo |

Một chiến lược lãi 40%/năm nhưng có 30% khả năng chạm max drawdown 10% là **chiến lược tệ** cho mục đích này. Một chiến lược lãi 15%/năm với 95% khả năng không chạm limit là **chiến lược tốt**.

Mọi quyết định thiết kế trong tài liệu này đều xuất phát từ nguyên tắc trên.

---

## 2. Luật chơi FTMO — ràng buộc thiết kế

> **Lưu ý cho AI đọc tài liệu này:** Các con số dưới đây là cấu hình phổ biến của gói FTMO Challenge tiêu chuẩn tại thời điểm viết tài liệu. FTMO có nhiều gói khác nhau (Normal, Aggressive, Swing) và có thể thay đổi điều khoản. **Luôn kiểm tra lại trên trang chủ FTMO trước khi đưa các con số này vào code.** Code phải đọc các giới hạn từ file config, không hard-code.

### 2.1. Các ràng buộc chính (gói Challenge tiêu chuẩn)

| Ràng buộc | Giá trị tham chiếu | Ý nghĩa với bot |
|---|---|---|
| **Profit target** | ~10% balance ban đầu | Phải đạt trong thời hạn quy định |
| **Max daily loss** | ~5% balance ban đầu | Tính theo ngày, reset mỗi ngày mới. **Vi phạm = fail ngay lập tức** |
| **Max total drawdown** | ~10% balance ban đầu | Tính từ đỉnh equity cao nhất hoặc balance ban đầu tùy gói. **Vi phạm = fail ngay lập tức** |
| **Minimum trading days** | thường có yêu cầu tối thiểu | Bot không được đạt target quá nhanh rồi ngừng |
| **Thời hạn** | thường có giới hạn ngày | Áp lực thời gian ảnh hưởng đến position sizing |

### 2.2. Hai điểm kỹ thuật cực kỳ quan trọng và hay bị hiểu sai

**(a) Daily loss thường tính trên equity, không chỉ balance.**
Nghĩa là floating loss của lệnh đang mở cũng được tính vào. Một bot chỉ kiểm tra balance sẽ vi phạm mà không biết. Risk module **bắt buộc** phải giám sát equity real-time.

**(b) Max drawdown có thể là trailing hoặc static tùy gói.**
- *Static*: tính từ balance ban đầu — dễ hơn, càng lãi càng an toàn.
- *Trailing*: tính từ đỉnh equity cao nhất từng đạt — khó hơn nhiều, lãi rồi vẫn có thể fail.

Phải xác định rõ gói đang dùng là loại nào trước khi viết logic guard, vì logic hoàn toàn khác nhau.

### 2.3. Ràng buộc về phương pháp giao dịch

FTMO có các điều khoản cấm hoặc hạn chế một số kiểu giao dịch. Các kiểu sau cần được kiểm tra kỹ với điều khoản hiện hành trước khi triển khai:

- Martingale / grid tăng lot sau lệnh thua
- Arbitrage độ trễ (latency arbitrage)
- Giao dịch khai thác lỗi giá của nhà cung cấp thanh khoản
- High-frequency scalping với thời gian giữ lệnh cực ngắn
- Copy trading giữa nhiều tài khoản
- Giao dịch tập trung quanh thời điểm tin tức quan trọng (một số gói hạn chế)

**Khuyến nghị thiết kế:** Tránh hoàn toàn martingale và grid. Không chỉ vì rủi ro vi phạm điều khoản, mà vì bản chất toán học của chúng tạo ra phân phối lợi nhuận đuôi dày — chính xác là thứ làm sụp đổ xác suất pass trong mô phỏng Monte Carlo.

---

## 3. Triết lý thiết kế cốt lõi

Đây là phần quan trọng nhất để AI tiếp nhận hiểu được "tại sao" đằng sau mọi quyết định kỹ thuật.

### 3.1. Risk management là tầng ưu tiên cao nhất, không phải là tính năng phụ

Kiến trúc phân tầng theo thứ tự ưu tiên:

```
Tầng 1 (cao nhất): Compliance Guard  — Chặn cứng mọi hành động vi phạm luật FTMO
Tầng 2:            Risk Manager      — Quyết định kích thước lệnh, có được vào lệnh không
Tầng 3:            Strategy Logic    — Quyết định KHI NÀO và HƯỚNG NÀO vào lệnh
Tầng 4 (thấp nhất): Execution        — Gửi lệnh tới broker
```

Quy tắc bất biến: **Tầng dưới không bao giờ override được tầng trên.** Chiến lược có thể muốn vào lệnh, nhưng nếu Risk Manager nói không, thì là không. Risk Manager có thể muốn vào lệnh size X, nhưng nếu Compliance Guard thấy điều đó có thể đẩy equity gần giới hạn, size bị cắt hoặc lệnh bị chặn.

Lý do: trong bài toán tối đa xác suất pass, một lệnh bị bỏ lỡ chỉ tốn cơ hội; một lệnh vi phạm limit làm mất toàn bộ dự án.

### 3.2. Cùng một risk module dùng chung cho backtest và live

Đây là quyết định kiến trúc then chốt. Risk module phải được viết sao cho:
- Backtest engine gọi nó để mô phỏng
- Live bot gọi nó để giao dịch thật

Nếu viết hai bản riêng, chắc chắn sẽ có sai lệch, và kết quả backtest trở nên vô nghĩa. Interface của risk module nên thuần túy: nhận vào trạng thái tài khoản + tín hiệu, trả ra quyết định. Không tự gọi API broker, không tự đọc file.

### 3.3. Mỗi kết quả backtest chỉ là một mẫu, không phải sự thật

Backtest trên dữ liệu lịch sử cho ra **một** đường equity duy nhất — một mẫu rút từ phân phối các kịch bản có thể xảy ra. Nó không trả lời được câu hỏi "xác suất tôi pass là bao nhiêu". Chỉ mô phỏng Monte Carlo mới trả lời được.

Hệ quả thực tế: **không bao giờ ra quyết định dựa trên một con số backtest đơn lẻ.** Mọi quyết định giữ/bỏ một thay đổi phải dựa trên phân phối kết quả Monte Carlo.

### 3.4. Mọi tham số nằm trong config, không nằm trong code

Tách biệt để khi thay đổi risk % không vô tình sửa logic chiến lược, và để mỗi lần chạy thí nghiệm có thể log lại chính xác bộ tham số đã dùng.

### 3.5. Ghi lại mọi thứ, kể cả thất bại

Chi tiết ở [mục 9](#9-hệ-thống-ghi-log-nghiên-cứu-hai-tầng). Nguyên tắc: một hướng đi đã thử và thất bại mà không được ghi lại sẽ bị thử lại sau vài tuần.

---

## 4. Lựa chọn công nghệ: Python vs MQL5

### 4.1. Quyết định: dùng cả hai, mỗi thứ cho một giai đoạn

| Giai đoạn | Công nghệ | Lý do |
|---|---|---|
| Nghiên cứu, backtest, Monte Carlo, tối ưu | **Python** | Hệ sinh thái phân tích dữ liệu mạnh, tốc độ lặp thí nghiệm nhanh |
| Chạy demo / forward test / FTMO Challenge | **MQL5 (Expert Advisor)** | Chạy native trong MT5, độ trễ thấp, ổn định 24/7, không phụ thuộc tiến trình ngoài |

### 4.2. Ưu điểm của Python (giai đoạn nghiên cứu)

- `pandas` / `numpy` cho xử lý dữ liệu giá và kết quả lệnh
- Thư viện backtest (`backtrader`, `vectorbt`) hoặc engine tự viết
- Monte Carlo, walk-forward analysis, phân tích độ nhạy tham số — viết bằng Python nhanh hơn MQL5 nhiều lần
- Dễ vẽ biểu đồ, dễ log, dễ tích hợp ML nếu cần sau này

### 4.3. Ưu điểm của MQL5 (giai đoạn chạy thật)

- EA chạy trong tiến trình MT5, không có lớp trung gian → độ trễ thấp
- Không cần tiến trình Python chạy song song → ít điểm hỏng hơn
- Nếu Python mất kết nối với MT5 khi đang có lệnh mở, hậu quả có thể rất nghiêm trọng — EA native không có rủi ro này
- FTMO yêu cầu giao dịch qua nền tảng MT4/MT5 của họ

### 4.4. Ràng buộc kỹ thuật cần lưu ý

- Thư viện `MetaTrader5` cho Python **chỉ chạy trên Windows** (phụ thuộc MT5 terminal). Nếu dùng VPS, phải là VPS Windows.
- Việc port code từ Python sang MQL5 là công đoạn có rủi ro sai lệch logic. Phải có bước đối chiếu: chạy cùng một khoảng dữ liệu trên cả hai, so sánh danh sách lệnh sinh ra. Xem [mục 10](#10-gate-criteria--tiêu-chí-chuyển-giai-đoạn).

---

## 5. Kiến trúc thư mục dự án

```
ftmo-bot/
├── README.md                    # Tổng quan, hướng dẫn chạy
├── .gitignore                   # Loại trừ data/, logs/, credentials
│
├── configs/                     # TẤT CẢ tham số nằm ở đây, không nằm trong code
│   ├── ftmo_rules.yaml          # Giới hạn của gói FTMO đang nhắm tới
│   ├── strategy_params.yaml     # Tham số chiến lược (SL, TP, filter...)
│   └── risk_params.yaml         # Risk % mỗi lệnh, buffer an toàn
│
├── data/                        # Dữ liệu giá lịch sử (KHÔNG commit lên git)
│   └── raw/
│
├── strategy/                    # Logic chiến lược thuần túy
│   ├── base.py                  # Interface chung cho mọi chiến lược
│   └── <ten_chien_luoc>.py      # Sinh tín hiệu: trả ra signal, không đặt lệnh
│
├── risk/                        # TRÁI TIM CỦA HỆ THỐNG
│   ├── risk_manager.py          # Position sizing, kiểm tra điều kiện vào lệnh
│   └── compliance_guard.py      # Chặn cứng theo luật FTMO
│
├── backtest/
│   ├── engine.py                # Chạy backtest, GỌI risk module giống hệt live
│   └── monte_carlo.py           # Mô phỏng phân phối kết quả
│
├── tools/
│   ├── experiment_logger.py     # Ghi log thí nghiệm tự động (đã có, xem phụ lục)
│   └── analyze_experiments.py   # Đọc log, so sánh các lần chạy
│
├── mql5_ea/                     # Code MQL5 sau khi port
│   ├── FtmoBot.mq5
│   └── RiskManager.mqh          # Bản port của risk module
│
├── logs/                        # Nhật ký chạy demo/live (KHÔNG commit)
├── reports/                     # Kết quả backtest, biểu đồ, báo cáo Monte Carlo
└── research_journal.md          # Nhật ký nghiên cứu viết tay (xem mục 9)
```

### Ghi chú kiến trúc

- `strategy/` **không** được biết gì về FTMO rules hay account balance. Nó chỉ nhận dữ liệu giá và trả ra tín hiệu. Điều này giúp thay chiến lược mà không phải sửa risk module.
- `risk/` **không** được gọi API broker. Nó nhận trạng thái tài khoản dưới dạng tham số và trả ra quyết định. Điều này cho phép backtest và live dùng chung.
- Ranh giới này phải được giữ nghiêm ngặt. Nếu thấy mình đang import broker API vào `strategy/`, đó là dấu hiệu kiến trúc đang bị phá vỡ.

---

## 6. Pipeline 6 giai đoạn

```
[1] Nghiên cứu chiến lược          Python, dữ liệu lịch sử, backtest thô
             │
             ▼
[2] Backtest & Monte Carlo         Đo P(pass FTMO), không chỉ đo lợi nhuận
             │
             ▼
[3] Risk Management Module         Xây guard cứng theo luật FTMO
             │
             ▼
[4] Port sang MQL5 EA              Viết lại logic native + đối chiếu kết quả
             │
             ▼
[5] Demo / Forward test            Chạy thật trên demo, so với backtest
             │
             ▼
[6] FTMO Challenge                 Chạy thật, giám sát liên tục
```

Mỗi mũi tên là một **gate** — chỉ được đi qua khi đạt tiêu chí định lượng ở [mục 10](#10-gate-criteria--tiêu-chí-chuyển-giai-đoạn). Không chuyển giai đoạn vì "cảm thấy ổn".

### Giai đoạn 1 — Nghiên cứu chiến lược

Mục tiêu: tìm ra một chiến lược có edge dương và ổn định.

Nội dung:
- Chọn thị trường (cặp tiền, vàng, chỉ số) và khung thời gian
- Chọn họ chiến lược (trend-following, mean-reversion, breakout...)
- Viết logic sinh tín hiệu trong `strategy/`
- Backtest thô để xác nhận có edge dương (chưa cần tối ưu)

Đầu ra: một chiến lược có profit factor > 1 trên dữ liệu lịch sử, và một danh sách kết quả từng lệnh.

**Cảnh báo overfitting:** Tối ưu tham số trên toàn bộ dữ liệu lịch sử sẽ cho kết quả đẹp nhưng vô dụng. Phải chia dữ liệu: in-sample để tối ưu, out-of-sample để kiểm chứng, và walk-forward analysis nếu có điều kiện.

### Giai đoạn 2 — Backtest & Monte Carlo

Mục tiêu: chuyển từ "chiến lược này lãi bao nhiêu" sang "chiến lược này có bao nhiêu % khả năng pass FTMO".

Chi tiết phương pháp ở [mục 8](#8-monte-carlo--phương-pháp-kiểm-định-chính).

### Giai đoạn 3 — Risk Management Module

Mục tiêu: xây tầng bảo vệ không cho phép vi phạm luật.

Chi tiết ở [mục 7](#7-risk-management-module--trái-tim-của-hệ-thống).

Lưu ý thứ tự: mặc dù risk module được liệt kê ở giai đoạn 3, trong thực tế nên viết **khung sơ bộ** của nó ngay từ giai đoạn 2, vì Monte Carlo phải mô phỏng cả cơ chế guard (bot dừng khi chạm daily limit làm thay đổi đường equity phía sau).

### Giai đoạn 4 — Port sang MQL5

Mục tiêu: có một EA chạy native trong MT5 với logic giống hệt bản Python.

Rủi ro lớn nhất: sai lệch logic khi port. Bắt buộc phải đối chiếu.

### Giai đoạn 5 — Demo / Forward test

Mục tiêu: xác nhận EA hoạt động đúng trong điều kiện thị trường thật (spread thay đổi, slippage, requote, gap cuối tuần) — những thứ backtest không mô phỏng đầy đủ.

Đây là giai đoạn hay bị bỏ qua và là nguyên nhân thất bại phổ biến.

### Giai đoạn 6 — FTMO Challenge

Chạy thật. Chi tiết vận hành ở [mục 11](#11-vận-hành-và-giám-sát-khi-chạy-live).

---

## 7. Risk Management Module — trái tim của hệ thống

### 7.1. Hai thành phần

**(a) `compliance_guard.py` — Chặn cứng**

Trả lời câu hỏi: "Hành động này có thể dẫn tới vi phạm luật FTMO không?"

Trách nhiệm:
- Theo dõi equity real-time (không chỉ balance)
- Tính daily loss đã sử dụng so với giới hạn, reset đúng thời điểm mốc ngày của broker
- Tính total drawdown so với giới hạn (theo đúng loại static/trailing của gói)
- Chặn mở lệnh mới khi đã dùng quá một ngưỡng an toàn của daily loss
- Đóng toàn bộ lệnh và ngừng giao dịch trong ngày khi chạm ngưỡng dừng khẩn cấp

Nguyên tắc thiết kế quan trọng: **luôn dùng buffer an toàn, không chạm sát giới hạn thật.**
Nếu daily limit là 5%, guard nên dừng ở khoảng 3–3.5%. Lý do: slippage, spread giãn, gap giá có thể làm loss vượt quá dự tính giữa thời điểm quyết định và thời điểm khớp lệnh. Buffer này phải là tham số trong config để Monte Carlo có thể kiểm định.

**(b) `risk_manager.py` — Quyết định kích thước**

Trả lời câu hỏi: "Nếu được phép vào lệnh, thì vào bao nhiêu?"

Trách nhiệm:
- Tính lot size từ: % rủi ro mỗi lệnh, khoảng cách stop loss, giá trị pip của symbol
- Giới hạn tổng rủi ro đồng thời khi có nhiều lệnh mở
- Xem xét tương quan giữa các cặp tiền (ví dụ EURUSD và GBPUSD thường cùng chiều — mở cả hai không phải là hai lệnh độc lập về rủi ro)
- Giảm size khi đã gần các ngưỡng cảnh báo

### 7.2. Interface đề xuất

Thiết kế sao cho backtest và live gọi giống hệt nhau:

```python
decision = risk_manager.evaluate(
    account_state=AccountState(
        balance=...,
        equity=...,
        initial_balance=...,
        peak_equity=...,
        daily_start_equity=...,
        open_positions=[...],
    ),
    signal=Signal(direction=..., entry=..., stop_loss=..., take_profit=...),
    ftmo_rules=rules_from_config,
)

# decision.allowed: bool
# decision.lot_size: float
# decision.reason: str   -- ghi vào log để truy vết sau này
```

Trường `reason` rất quan trọng: khi bot không vào lệnh, bạn cần biết là do chiến lược không có tín hiệu, hay do risk module chặn, và chặn vì lý do gì.

### 7.3. Kiểm thử riêng cho risk module

Risk module phải có test riêng, độc lập với chiến lược. Các kịch bản tối thiểu cần test:

- Giả lập chuỗi thua liên tiếp → guard có dừng đúng ngưỡng không?
- Giả lập gap giá qua đêm gây loss lớn → guard có xử lý được không?
- Giả lập mốc reset ngày mới → daily loss có reset đúng thời điểm broker không?
- Giả lập equity tăng rồi giảm → trailing drawdown có tính đúng từ đỉnh không?
- Giả lập nhiều lệnh mở cùng lúc → tổng rủi ro có bị vượt không?

Đây là phần code duy nhất trong dự án mà việc viết unit test là **bắt buộc**, không phải tùy chọn.

---

## 8. Monte Carlo — phương pháp kiểm định chính

### 8.1. Vấn đề cần giải quyết

Backtest cho ra một đường equity. Nhưng thứ tự các lệnh thắng/thua trong tương lai sẽ khác. Câu hỏi thực sự quan trọng là: **nếu cùng bộ lệnh đó xảy ra theo thứ tự khác, có bao nhiêu % khả năng vi phạm limit?**

Minh họa: cùng 100 kết quả lệnh y hệt nhau, chỉ xáo trộn thứ tự, cho ra các đường equity có hình dạng và mức drawdown hoàn toàn khác nhau. Backtest gốc chỉ cho bạn một trong số đó.

### 8.2. Các phương pháp mô phỏng

| Phương pháp | Cách làm | Kiểm định điều gì |
|---|---|---|
| **Reshuffle thứ tự lệnh** | Hoán vị ngẫu nhiên danh sách kết quả lệnh | Rủi ro do trình tự thắng/thua xấu |
| **Bootstrap (lấy mẫu có hoàn lại)** | Lấy ngẫu nhiên có lặp từ tập kết quả | Kịch bản chuỗi thua tệ hơn cả lịch sử |
| **Random bỏ bớt lệnh** | Loại ngẫu nhiên một tỷ lệ lệnh | Rủi ro khi VPS/internet lỗi, bỏ lỡ tín hiệu |
| **Biến đổi win rate / R:R** | Điều chỉnh nhẹ tham số phân phối | Độ nhạy khi thị trường tương lai khác quá khứ |
| **Thêm slippage ngẫu nhiên** | Trừ một lượng ngẫu nhiên vào mỗi lệnh | Rủi ro thực thi trong điều kiện thật |

### 8.3. Điểm mấu chốt: phải mô phỏng kèm cơ chế guard

Đây là sai lầm phổ biến nhất khi làm Monte Carlo cho FTMO.

**Cách sai:** Tính đường equity từ chuỗi lệnh, rồi so sánh max drawdown với giới hạn 10%.

**Cách đúng:** Trong vòng lặp mô phỏng, áp dụng cả cơ chế guard — khi equity chạm ngưỡng daily loss, bot dừng giao dịch phần còn lại của ngày, nên các lệnh tiếp theo trong ngày đó không xảy ra. Điều này làm thay đổi toàn bộ đường equity phía sau.

Nếu không mô phỏng guard, kết quả Monte Carlo sẽ sai lệch theo cả hai hướng: đánh giá quá cao rủi ro vi phạm drawdown, và đánh giá quá cao khả năng đạt profit target.

### 8.4. Đầu ra cần thu thập

Với mỗi lần chạy Monte Carlo (khuyến nghị tối thiểu 1000 lần mô phỏng), thu thập:

- `p_pass`: tỷ lệ % số lần mô phỏng đạt profit target mà không vi phạm limit nào — **đây là chỉ số quan trọng nhất**
- `p_fail_daily_loss`: % số lần fail vì vi phạm daily loss
- `p_fail_max_dd`: % số lần fail vì vi phạm max drawdown
- `p_timeout`: % số lần không vi phạm gì nhưng không đạt target kịp thời hạn
- Phân phối max drawdown: trung bình, percentile 5/50/95
- Phân phối số ngày cần để đạt target

Việc tách riêng các nguyên nhân fail rất hữu ích cho việc chẩn đoán: nếu fail chủ yếu vì timeout thì cần tăng size hoặc tăng tần suất giao dịch; nếu fail vì daily loss thì cần giảm size hoặc siết guard.

### 8.5. Ví dụ code tham chiếu (bản rút gọn)

```python
import numpy as np

def monte_carlo_ftmo(trade_results, rules, n_sims=1000, seed=42):
    """
    trade_results: mảng % thay đổi equity của từng lệnh, lấy từ backtest
    rules: dict chứa profit_target_pct, daily_loss_pct, max_dd_pct, ...

    LƯU Ý: bản rút gọn này chưa mô phỏng cơ chế guard theo ngày.
    Bản đầy đủ phải nhóm lệnh theo ngày và dừng giao dịch khi chạm daily limit.
    """
    rng = np.random.default_rng(seed)
    outcomes = {"pass": 0, "fail_dd": 0, "timeout": 0}
    max_dds = []

    for _ in range(n_sims):
        shuffled = rng.permutation(trade_results)
        equity = 100 * np.cumprod(1 + shuffled / 100)
        running_max = np.maximum.accumulate(np.concatenate([[100], equity]))[1:]
        dd = (equity - running_max) / running_max * 100
        max_dds.append(dd.min())

        if dd.min() <= -rules["max_dd_pct"]:
            outcomes["fail_dd"] += 1
        elif equity.max() >= 100 + rules["profit_target_pct"]:
            outcomes["pass"] += 1
        else:
            outcomes["timeout"] += 1

    return {
        "p_pass": outcomes["pass"] / n_sims,
        "p_fail_dd": outcomes["fail_dd"] / n_sims,
        "p_timeout": outcomes["timeout"] / n_sims,
        "max_dd_mean": float(np.mean(max_dds)),
        "max_dd_p95": float(np.percentile(max_dds, 5)),  # 5th percentile = tệ nhất
    }
```

Bản đầy đủ cần bổ sung: nhóm lệnh theo ngày giao dịch, áp dụng daily loss guard, xử lý minimum trading days, và mô phỏng slippage.

---

## 9. Hệ thống ghi log nghiên cứu hai tầng

Đây là hệ thống được thiết kế để tránh vấn đề phổ biến nhất trong nghiên cứu chiến lược cá nhân: sau vài tuần, không còn nhớ đã thử gì, tại sao chọn tham số hiện tại, và vô tình thử lại những hướng đã thất bại.

Phương pháp mô phỏng cách làm việc của researcher thực thụ: **structured experiment log** (để so sánh định lượng) kết hợp **lab notebook** (để giữ mạch tư duy). Thiếu một trong hai đều gây vấn đề.

### 9.1. Tầng 1 — Research Journal (viết tay, dạng tường thuật)

File: `research_journal.md`

Mỗi entry theo cấu trúc khoa học: **giả thuyết → thay đổi → kết quả → diễn giải → quyết định → bước tiếp**.

Quy tắc bất biến: **viết giả thuyết TRƯỚC khi chạy thí nghiệm.** Nếu viết sau khi đã thấy kết quả, sẽ rơi vào bias tự hợp lý hóa — luôn tìm được lý do giải thích bất cứ kết quả nào, và mất hoàn toàn giá trị của việc kiểm định giả thuyết.

Template:

```markdown
## Entry #001 — YYYY-MM-DD

**Run ID liên kết:** run_xxxxxxxx

**Giả thuyết (viết TRƯỚC khi chạy):**
> Nếu tăng bộ lọc ATR để tránh vào lệnh lúc volatility thấp, win rate sẽ tăng
> nhưng số lệnh/tháng giảm, có thể ảnh hưởng đến khả năng đạt profit target.

**Thay đổi cụ thể:**
- atr_filter_threshold: 0.0015 -> 0.0025

**Kết quả:**
- (dán số liệu hoặc link tới report)

**Diễn giải — đúng hay sai giả thuyết? Vì sao?**
-

**Quyết định:** [ ] Giữ  [ ] Revert  [ ] Cần test thêm

**Bước tiếp theo:**
-
```

Ngoài ra, cuối mỗi tuần nên đọc lại toàn bộ entry trong tuần và viết một đoạn tổng kết: pattern nào lặp lại, hướng nào đang bế tắc, hướng nào đáng đào sâu.

**Không xóa entry thất bại.** Chúng là phần giá trị nhất của journal.

### 9.2. Tầng 2 — Experiment Log (máy tự ghi, dạng bảng)

File: `experiments_log.csv`, sinh tự động bởi `tools/experiment_logger.py`.

Mỗi lần chạy backtest hoặc Monte Carlo, code tự động append một dòng gồm:

| Cột | Nội dung |
|---|---|
| `run_id` | ID duy nhất, dùng để liên kết với journal |
| `timestamp_utc` | Thời điểm chạy |
| `git_commit` | Hash commit của code đã chạy — cho phép tái tạo chính xác |
| `params_json` | Toàn bộ tham số đầu vào |
| `metrics_json` | Toàn bộ kết quả đo được |
| `notes` | Ghi chú ngắn, trỏ tới entry journal tương ứng |

Việc ghi `git_commit` là điểm then chốt: khi thấy một lần chạy cũ có kết quả tốt, bạn có thể quay lại chính xác version code đó.

Cách dùng:

```python
from tools.experiment_logger import log_experiment

run_id = log_experiment(
    params={"risk_per_trade_pct": 0.5, "atr_filter_threshold": 0.0025,
            "symbol": "XAUUSD", "timeframe": "M15"},
    metrics={"win_rate": 0.47, "profit_factor": 1.35, "max_drawdown_pct": -6.8,
             "monte_carlo_p_pass": 0.91, "num_trades": 142},
    notes="Thử tăng ATR filter theo giả thuyết ở journal entry #007",
)
```

Sau đó dán `run_id` vào entry journal tương ứng. Hai tầng log liên kết với nhau qua ID này.

### 9.3. Phân tích log

Cuối mỗi tuần hoặc khi cần ra quyết định:

```python
import pandas as pd
from tools.experiment_logger import load_experiments

df = pd.DataFrame(load_experiments())
# Trích metrics ra thành cột riêng để sort/filter
metrics_df = pd.json_normalize(df["metrics"])
params_df = pd.json_normalize(df["params"])
full = pd.concat([df[["run_id", "git_commit", "notes"]], params_df, metrics_df], axis=1)

# Ví dụ: xem các cấu hình có xác suất pass cao nhất
full.sort_values("monte_carlo_p_pass", ascending=False).head(10)
```

Câu hỏi nên đặt ra khi phân tích: có tham số nào mà kết quả tốt chỉ xuất hiện ở một giá trị rất cụ thể không? Nếu có, đó là dấu hiệu overfitting — chiến lược tốt phải có vùng tham số ổn định, không phải một điểm nhọn.

### 9.4. Log vận hành (khác với log nghiên cứu)

Khi bot chạy demo/live, cần một loại log khác — log vận hành, ghi vào `logs/`:

- Mọi lệnh: thời điểm, hướng, size, entry, SL, TP, lý do vào lệnh
- Mọi lần risk module chặn: thời điểm, lý do chặn (trường `reason`)
- Trạng thái risk theo chu kỳ: % daily loss đã dùng, % drawdown hiện tại, số lệnh đang mở
- Mọi lỗi kỹ thuật: mất kết nối, requote, lệnh bị từ chối

Log này phục vụ chẩn đoán khi có sự cố, và để đối chiếu kết quả forward test với backtest.

---

## 10. Gate criteria — tiêu chí chuyển giai đoạn

Nguyên tắc: mỗi gate phải là tiêu chí **định lượng, xác định trước**, không phải đánh giá chủ quan. Viết ra tiêu chí trước khi chạy thí nghiệm, không điều chỉnh tiêu chí sau khi thấy kết quả.

Các ngưỡng dưới đây là đề xuất khởi điểm, cần điều chỉnh theo bối cảnh cụ thể của dự án.

### Gate 1→2: Chiến lược có đáng kiểm định sâu không?

- [ ] Profit factor > 1.2 trên dữ liệu out-of-sample (không phải in-sample)
- [ ] Số lệnh đủ lớn để có ý nghĩa thống kê (tối thiểu ~100 lệnh)
- [ ] Edge không đến từ một vài lệnh ngoại lệ (kiểm tra: bỏ 5 lệnh lãi nhất, còn dương không?)
- [ ] Kết quả ổn định qua các giai đoạn thị trường khác nhau, không chỉ một xu hướng

### Gate 2→3: Chiến lược có đủ khả năng pass không?

- [ ] `p_pass` từ Monte Carlo ≥ 85–90% qua tối thiểu 1000 mô phỏng
- [ ] `p_pass_across_history` từ rolling-window backtest ≥ 80%. Nếu chênh lệch lớn so với Monte Carlo, ưu tiên đánh giá theo chỉ số thấp hơn vì nó phản ánh rủi ro chế độ thị trường.
- [ ] Soft-stop tầng Critical đã được xác nhận kích hoạt đúng trong ít nhất một vài window lịch sử có biến động mạnh.
- [ ] `p_fail_daily_loss` và `p_fail_max_dd` đều ở mức chấp nhận được
- [ ] Kết quả ổn định khi thay đổi seed ngẫu nhiên
- [ ] Vùng tham số ổn định (không phải điểm nhọn — dấu hiệu overfitting)

### Gate 3→4: Risk module đã đủ tin cậy chưa?

- [ ] Toàn bộ unit test ở [mục 7.3](#73-kiểm-thử-riêng-cho-risk-module) đều pass
- [ ] Đã test kịch bản cực đoan: gap giá lớn, chuỗi thua dài, nhiều lệnh mở đồng thời
- [ ] Monte Carlo chạy lại có tích hợp guard, `p_pass` vẫn đạt ngưỡng
- [ ] Buffer an toàn đã được kiểm định, không chỉ chọn theo cảm tính

### Gate 4→5: Bản port MQL5 có khớp với bản Python không?

- [ ] Chạy cả hai trên cùng một khoảng dữ liệu lịch sử
- [ ] Danh sách lệnh sinh ra khớp nhau (cho phép sai lệch nhỏ do làm tròn/spread)
- [ ] Các chỉ số tổng hợp (số lệnh, win rate, max DD) lệch dưới ngưỡng cho phép
- [ ] Đã test EA xử lý đúng các tình huống kỹ thuật: mất kết nối, restart MT5, requote

### Gate 5→6: Forward test có xác nhận backtest không?

- [ ] Chạy demo tối thiểu 4–6 tuần liên tục (không ngắt quãng, không can thiệp tay)
- [ ] Kết quả forward test nằm trong khoảng phân phối mà Monte Carlo dự đoán
- [ ] Không có sự cố kỹ thuật nghiêm trọng nào chưa được xử lý
- [ ] Risk module đã thực sự kích hoạt ít nhất một lần trong điều kiện thật và hoạt động đúng
- [ ] Slippage và spread thực tế không làm sai lệch đáng kể so với giả định backtest

**Lưu ý về gate cuối:** Nếu forward test cho kết quả lệch xa backtest, đừng vội điều chỉnh tham số để khớp. Đó thường là dấu hiệu backtest đã overfit hoặc có lỗi giả định (look-ahead bias, spread không thực tế). Quay lại giai đoạn 1–2 để chẩn đoán.

---

## 11. Vận hành và giám sát khi chạy live

### 11.1. Hạ tầng

- **VPS** đặt gần server broker để giảm độ trễ, chạy 24/5 không gián đoạn
- Nếu dùng Python trong pipeline: bắt buộc VPS Windows
- Cấu hình tự khởi động lại MT5 và EA sau khi VPS reboot
- Đồng hồ hệ thống đồng bộ chính xác (ảnh hưởng tới mốc reset ngày)

### 11.2. Giám sát

Bot phải tự báo cáo, không nên phải mở MT5 để kiểm tra thủ công. Tối thiểu cần:

- **Cảnh báo tức thời** (Telegram/Discord/email) khi: mở lệnh, đóng lệnh, risk module chặn lệnh, chạm ngưỡng cảnh báo, có lỗi kỹ thuật
- **Báo cáo định kỳ** cuối mỗi ngày giao dịch: số lệnh, P&L, % daily loss đã dùng, % drawdown hiện tại, khoảng cách còn lại tới profit target
- **Dashboard trạng thái** (tùy chọn): xem nhanh các chỉ số quan trọng

Chỉ số quan trọng nhất cần theo dõi liên tục: **khoảng cách còn lại tới mỗi giới hạn**, không phải P&L.

### 11.3. Quy tắc can thiệp tay

Xác định trước, viết ra giấy, khi nào được phép can thiệp:

- Được phép: dừng bot khi phát hiện lỗi kỹ thuật rõ ràng (sai symbol, sai size, lệnh lặp)
- Được phép: dừng bot trước sự kiện thị trường bất thường đã biết trước
- **Không được phép:** can thiệp vì cảm thấy lo lắng khi bot đang thua theo đúng kế hoạch
- **Không được phép:** tăng size để "gỡ lại" khi gần hết thời hạn

Việc viết trước các quy tắc này khi đầu óc tỉnh táo là biện pháp bảo vệ chống lại quyết định cảm tính khi đang chịu áp lực.

### 11.4. Nhật ký giao dịch trong lúc challenge

Duy trì ghi chép hàng ngày: trạng thái tài khoản, sự kiện bất thường, cảm nhận về hành vi của bot. Đây là dữ liệu quý giá để cải thiện cho lần sau, dù pass hay fail.

---

## 12. Những cạm bẫy đã biết

Danh sách này nên được cập nhật liên tục trong quá trình dự án.

### 12.1. Về nghiên cứu

| Cạm bẫy | Biểu hiện | Cách phòng |
|---|---|---|
| **Overfitting** | Backtest đẹp bất thường, kết quả sụp khi đổi khoảng dữ liệu | Out-of-sample test, walk-forward, kiểm tra vùng tham số ổn định |
| **Look-ahead bias** | Code vô tình dùng dữ liệu tương lai (ví dụ giá đóng cửa của nến hiện tại) | Rà soát kỹ logic, chỉ dùng dữ liệu đã hoàn tất tại thời điểm quyết định |
| **Survivorship bias trong tối ưu** | Thử 200 biến thể, chọn cái tốt nhất, tưởng đó là edge | Ghi log mọi lần thử, xem cái tốt nhất có vượt trội có ý nghĩa không |
| **Giả định spread cố định** | Backtest dùng spread lý tưởng, thực tế spread giãn lúc tin tức | Mô phỏng spread thay đổi, thêm slippage vào Monte Carlo |
| **Quá ít lệnh** | Kết luận từ 30 lệnh | Yêu cầu tối thiểu ~100 lệnh, ưu tiên nhiều hơn |

### 12.2. Về triển khai

| Cạm bẫy | Biểu hiện | Cách phòng |
|---|---|---|
| **Sai lệch khi port Python→MQL5** | EA giao dịch khác bản backtest | Đối chiếu bắt buộc ở Gate 4→5 |
| **Daily loss tính theo balance thay vì equity** | Vi phạm mà bot không biết | Guard phải giám sát equity |
| **Sai mốc reset ngày** | Reset theo giờ local thay vì giờ broker | Xác nhận mốc ngày của broker, test kỹ |
| **Không xử lý gap cuối tuần** | Lệnh giữ qua cuối tuần gặp gap lớn | Cân nhắc đóng lệnh trước cuối tuần, hoặc mô phỏng gap trong Monte Carlo |
| **Không có buffer an toàn** | Chạm limit do slippage dù logic tính đúng | Dừng ở ngưỡng thấp hơn limit thật |

### 12.3. Về tâm lý và quy trình

| Cạm bẫy | Biểu hiện | Cách phòng |
|---|---|---|
| **Điều chỉnh tiêu chí sau khi thấy kết quả** | Hạ ngưỡng gate để được đi tiếp | Viết gate criteria trước, không sửa |
| **Bỏ qua forward test** | Nhảy thẳng từ backtest vào challenge | Coi Gate 5 là bắt buộc |
| **Can thiệp tay khi bot đang thua** | Phá vỡ giả định của toàn bộ nghiên cứu | Quy tắc can thiệp viết trước |
| **Không ghi log thất bại** | Lặp lại hướng đã thử | Journal giữ nguyên mọi entry |

---

## 13. Trạng thái hiện tại và bước tiếp theo

### 13.1. Đã hoàn thành

- Thống nhất kiến trúc tổng thể và triết lý thiết kế (ưu tiên xác suất pass hơn lợi nhuận)
- Quyết định công nghệ: Python cho nghiên cứu, MQL5 cho vận hành
- Thiết kế pipeline 6 giai đoạn với gate criteria
- Xây dựng hệ thống log nghiên cứu hai tầng (`research_journal.md` + `experiment_logger.py`)
- Làm rõ phương pháp Monte Carlo và điểm mấu chốt về mô phỏng kèm guard

### 13.2. Chưa quyết định

Những điểm sau cần được làm rõ trước khi bắt đầu giai đoạn 1:

- **Gói FTMO cụ thể** (ảnh hưởng trực tiếp tới các con số trong `ftmo_rules.yaml`, và tới việc drawdown là static hay trailing)
- **Thị trường và khung thời gian** (forex majors, vàng, chỉ số — mỗi loại có đặc tính volatility và spread khác nhau)
- **Họ chiến lược** (trend-following, mean-reversion, breakout — chưa chọn)

### 13.3. Bước tiếp theo đề xuất

1. Xác định ba điểm ở mục 13.2
2. Tạo cấu trúc thư mục và khởi tạo git repo
3. Viết `configs/ftmo_rules.yaml` với các giới hạn của gói đã chọn
4. Viết khung sơ bộ của risk module (đủ để Monte Carlo mô phỏng guard)
5. Lấy dữ liệu lịch sử và viết chiến lược đầu tiên trong `strategy/`
6. Chạy backtest thô + Monte Carlo, log lại bằng `experiment_logger.py`, ghi entry đầu tiên vào journal

---

## 14. Phụ lục: Code tham chiếu

### 14.1. `tools/experiment_logger.py`

Script ghi log thí nghiệm tự động. Các hàm chính:

- `log_experiment(params, metrics, notes) -> run_id` — ghi một dòng mới, tự động lấy git commit hash và timestamp UTC
- `load_experiments() -> list[dict]` — đọc toàn bộ log, parse sẵn `params` và `metrics` thành dict
- `best_by(metric_key, top_n, higher_is_better)` — in ra top N lần chạy tốt nhất theo một metric

Phụ thuộc: chỉ dùng thư viện chuẩn của Python (`csv`, `json`, `subprocess`, `uuid`, `datetime`, `pathlib`). Không cần cài thêm gì.

### 14.2. `research_journal.md`

Template nhật ký nghiên cứu dạng tường thuật, cấu trúc đã mô tả ở [mục 9.1](#91-tầng-1--research-journal-viết-tay-dạng-tường-thuật).

---

## Ghi chú cuối cho AI tiếp nhận tài liệu

Nếu bạn là một AI assistant được giao tiếp tục dự án này, những điểm sau đáng lưu ý:

1. **Đừng nhảy thẳng vào việc viết chiến lược sinh lời.** Kiến trúc và risk module quan trọng hơn. Một chiến lược tầm thường với risk management tốt có xác suất pass cao hơn một chiến lược xuất sắc không có guard.

2. **Mọi con số về luật FTMO trong tài liệu này cần được kiểm chứng lại** với điều khoản hiện hành, vì chúng có thể đã thay đổi.

3. **Khi người dùng đề xuất một thay đổi, hãy hỏi giả thuyết trước.** Điều này phù hợp với phương pháp làm việc đã thống nhất ở mục 9, và giúp tránh việc thử ngẫu nhiên không có định hướng.

4. **Cảnh giác với các đề xuất tăng rủi ro để đạt target nhanh hơn.** Chúng thường xuất hiện khi gần hết thời hạn và là nguyên nhân thất bại phổ biến.

5. **Giao dịch tài chính có rủi ro mất vốn.** Không có chiến lược nào đảm bảo pass, và kết quả backtest không đảm bảo kết quả tương lai. Tài liệu này mô tả một quy trình kỹ thuật, không phải lời khuyên đầu tư.
