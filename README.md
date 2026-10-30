# Tool săn boss NroChibi

Tool tự động dò khu, tìm boss và đánh cho game NroChibi (private server).
Chạy được nhiều account cùng lúc, mỗi acc phụ trách một dải khu riêng.

Tool **chỉ đọc pixel trên màn hình** và **gửi input như người dùng thật**.
Không đọc bộ nhớ tiến trình, không can thiệp gói tin mạng của game.

---

## 1. Chạy tool

Bấm đôi lối tắt **San Boss Nro** trên desktop.

Hoặc gõ trong PowerShell:

```powershell
cd D:\code\nro-boss-tool
python boss_hunter.py --zones 0-25
```

Tool tự nhận **tất cả** cửa sổ game đang mở, không cần khai báo số acc.

### Chuẩn bị

1. Mở game, đăng nhập các nick, đưa **tất cả vào cùng một map có boss**.
2. Kiểm tra bằng cách nhìn góc trên trái màn hình, chỗ hiện
   `183.Thung lung bong toi - K0`.
3. Bấm **BAT** trên cửa sổ điều khiển, hoặc bấm phím **F9** trong game.

Trên màn hình game có một ô trạng thái nổi ở góc trên phải, bấm vào đó
cũng bật/tắt được.

| Phím | Tác dụng |
|---|---|
| `F9` | Bật/tắt tool (hoạt động cả khi đang chơi game) |
| `F11` | Tạm dừng / tiếp tục |
| `F12` | Dừng khẩn cấp |

### Tuỳ chọn

```powershell
python boss_hunter.py --zones 0-25 --accounts 5     # giới hạn 5 acc
python boss_hunter.py --zones 0-25 --window-list 0,2,3,4,5   # chỉ dùng các cửa sổ này
python boss_hunter.py --list-windows               # xem danh sách cửa sổ game
python boss_hunter.py --dry-run                   # xem kế hoạch chia khu, không chạy
python boss_hunter.py --no-panel                   # không mở cửa sổ điều khiển
```

## 2. Cài đặt lần đầu

```powershell
cd D:\code\nro-boss-tool
python -m pip install opencv-python mss pywin32
```

## 3. Cách hoạt động

1. Mỗi acc phụ trách một dải khu, dò bằng lệnh chat `k X`. `--zones 0-25`
   là dải **tối đa**; tool tự nhận ra map thật sự có bao nhiêu khu.
2. Vào mỗi khu, tool spam ENTER để đánh. Nếu khu đó có boss, game hiện
   **dòng chữ đỏ to đùng ở giữa màn hình**, ví dụ `Tho dai ca [467/500]`.
3. Acc tìm thấy báo khu đó cho cả đội, các acc khác lập tức chuyển sang.
4. Tất cả liên tục ấn `ENTER` đánh boss.
5. Tool theo dõi thanh máu boss. Khi **thanh máu biến mất hẳn** hoặc
   **máu về 0**, tool chờ thêm `boss_phase_gap` giây (mặc định 10 giây)
   vì boss nhiều phase có thể mất một lúc mới hiện phase tiếp theo, và lúc
   mới chuyển phase boss có thể bất tử. Nếu thanh máu hiện lại, tool đánh
   tiếp; hết thời gian chờ mà vẫn không thấy mới coi là boss đã chết, báo
   cho cả đội và tất cả quay lại dò khu từ đầu.

### Map ít hơn 25 khu

Một số map chỉ có ít khu hơn dải `0-25`. Tool tự phát hiện: nếu gửi `k X`
mà dòng chỉ số khu vẫn không đổi sau `zone_missing_confirmations` lần thử ở
các vòng dò khác nhau (mặc định 2 lần, không tính lỗi game bắt chờ), tool
coi khu `X` không tồn tại trên map. Khi đó tool:

- thu hẹp dải khu còn `0..X-1`,
- chia lại khu cho tất cả acc theo vòng tròn,
- đưa mọi acc về khu đầu tiên của dải mới.

Nhờ vậy tool không dò các khu ảo, và khi đã dò hết khu cuối mà không thấy
boss thì tự quay lại khu đầu tiên. Nếu biết chắc map có ít khu, có thể chỉ
định dải nhỏ hơn cho nhanh, ví dụ `--zones 0-14`.

Ví dụ map có 26 khu, chia vòng tròn để nhiều acc quét song song:

```
acc 0: 0, 5, 10, 15, 20, 25
acc 1: 1, 6, 11, 16, 21
acc 2: 2, 7, 12, 17, 22
```

Với map ít khu hơn, danh sách được chia lại trong dải thật. Sau khi đổi map
bằng nút **DONG BO**, tool xoá giới hạn khu đã học để nhận lại số khu của
map mới.

### Nhận diện boss

Tool không đọc chữ, chỉ so sánh màu và vị trí:

- Dòng chữ đỏ phải **rộng** (dạng `Tên [số / số]`, từ 140px trở lên).
- Dòng phải **căn giữa màn hình**, giống cách game vẽ thanh máu boss.
- Phải xuất hiện **liên tục nhiều ảnh liên tiếp** mới được coi là boss.

Ba điều kiện này loại được số sát thương (`-314685`), vốn cũng là chữ đỏ
nhưng hẹp, lệch sang cạnh nhân vật và biến mất trong chưa tới 1 giây.

### Đọc chỉ số khu (nguồn xác nhận duy nhất)

Góc trên trái cửa sổ game luôn hiện dòng `154.Hành tỉnh Bill - K20`.
Tool đọc đúng dòng này để biết acc đang đứng ở khu nào, **không đoán theo
lệnh đã gửi**.

Gửi lệnh `k X` không có nghĩa là đã đổi khu: game có thể hiện hộp thoại
`Không thể đổi khu vực lúc này` và giữ nguyên khu cũ. Vì vậy mỗi lần đổi
khu, tool làm như sau:

1. Chụp dòng chỉ số khu trước khi gửi lệnh.
2. Gửi `k X`.
3. Chờ dòng chỉ số khu **thật sự đổi khác** (xác nhận 2 lần liên tiếp).
4. Nếu thấy hộp thoại chặn → đóng, chờ, gửi lại (tối đa 3 lần).
5. Chỉ khi đó mới coi là đã đổi khu; nếu không thì giữ nguyên khu **thật**
   và ghi log `KHONG DOI DUOC KHU`.

Tool **không dùng OCR**. Mỗi khu được nhận ra bằng chính hình ảnh dòng chữ
đó (nhị phân hoá, so khớp từng cột), và tool **tự học** ngay lần đầu tới
một khu. Không cần cài thêm gì.

Muốn kiểm tra vùng cắt có đúng không:

```powershell
python boss_hunter.py --dump-zone
```

Lệnh này lưu `zone_crop_0.png` — mở lên phải thấy đúng dòng chỉ số khu.

### Nhận diện boss đã chết

Tool theo dõi thanh máu theo hai cách:

- **Thanh máu biến mất hẳn** khỏi màn hình: đây là tín hiệu rõ ràng nhất,
  acc đang đánh thấy mất là báo ngay cho cả đội.
- **Máu về 0**: game có lúc giữ thanh máu đứng lại ở giá trị `0` thay vì
  xoá hẳn. Tool tìm dấu `/` trong dòng `Tên [hp / max]` rồi đếm số ký
  tự của nhóm máu hiện tại: nếu chỉ còn 1 chữ số (tức là `0`) thì coi như
  boss đã chết. Cách này không cần đọc chữ số nên không bị lỗi khi đọc
  nhầm số.

Ngoài ra, nếu không acc nào thấy boss trong `boss_gone_timeout` giây thì
trạng thái chung tự hết hạn và các acc quay lại dò khu. Đây là lưới an
toàn phụ cho trường hợp thanh máu bị che hoặc đọc sai.

## 4. Đồng bộ bàn phím — đưa cả đội sang map khác

Nút **DONG BO** trên cửa sổ điều khiển (hoặc bấm lại lần nữa để tắt).

Chế độ này **chỉ đồng bộ bàn phím, không đồng bộ chuột** và cần ít nhất
2 cửa sổ game.

Cách dùng: bật **DONG BO** → bấm vào **một** cửa sổ game rồi gõ phím bình
thường. Các cửa sổ game còn lại sẽ nhận đúng chuỗi phím đó.

- Chỉ nhân bản khi cửa sổ game đang có focus — bạn gõ ở Chrome, VS Code
  hay panel của tool thì không bị copy.
- **Mọi cửa sổ game đang mở đều nhận phím**, kể cả cửa sổ đang thu nhỏ
  (tool tự khôi phục trước khi gửi). Cửa sổ game mở thêm sau khi bật đồng
  bộ cũng tự được thêm vào danh sách.
- Game chỉ nhận phím khi cửa sổ đang focus, nên tool lần lượt đưa từng
  cửa sổ game lên trước để gửi phím thật. Các cửa sổ sẽ **nháy focus**;
  đây là cách duy nhất để game thực sự nhận phím. Mỗi cửa sổ được thử lấy
  focus vài lần; acc nào vẫn không nhận được phím sẽ hiện cảnh báo trong
  log kèm số hiệu acc.
- Tool chờ bạn ngừng gõ khoảng 0,12 giây rồi mới gửi cả cụm phím, để một
  lệnh như `k 12` + ENTER không bị cắt giữa chừng.
- Nếu bạn gõ tiếp đúng lúc tool đang gửi phím cho các acc khác, các phím
  đó được **giữ lại và gửi ở lượt sau** cho tất cả các acc, thay vì rơi
  vào đúng một cửa sổ đang tạm có focus.
- Trong lúc đồng bộ, tool tự **ngừng gõ phím** để không tranh chỗ với bạn.
- Tắt đồng bộ (bấm lại nút, hoặc `F12`) là tool quay lại dò khu bình thường.
  Nếu bạn vừa đổi sang map khác, tool tự xoá giới hạn khu cũ và dò lại từ
  khu đầu tiên của map mới.
- Cách này phù hợp với bấm phím, gõ chat, mở menu. Giữ phím di chuyển liên
  tục (W/A/S/D) có thể không mượt vì mỗi lần đổi focus sẽ ngắt trạng thái giữ phím.

Nút **BOSS DA CHET** dùng khi bạn thấy boss đã chết mà tool vẫn tưởng còn
sống: nó xoá trạng thái boss chung, đặt mọi acc dò lại **từ khu đầu tiên**
của acc đó và xoá khung log. Tool cũng tự làm việc này khi chính nó xác
nhận boss đã chết.

Nút **XOA LOG + RESET** xoá nội dung khung log và cũng đặt mọi acc dò
lại từ khu đầu tiên, tiện khi bắt đầu một lượt săn mới.

## 5. Cách gửi phím (quan trọng)

Game NroChibi **chỉ xử lý phím khi cửa sổ game đang có focus**. Vì vậy
tool mặc định giành focus trước khi gõ (cách này đã chạy ổn định trên game
thật). Khi chạy nhiều acc, các cửa sổ sẽ lần lượt được đưa lên trước —
đây là cái giá phải trả để lệnh thực sự được gửi.

Kiểm tra máy/game có nhận phím bằng `PostMessage` (không cần focus) không:

```powershell
python boss_hunter.py --test-keys
```

- `NHAN phim` ở **tất cả** cửa sổ: có thể chạy `--post-keys` để cửa sổ
  đứng yên, không nhảy.
- `KHONG nhan phim`: giữ nguyên mặc định (giành focus), đừng dùng
  `--post-keys`, nếu không lệnh đổi khu sẽ bị game bỏ qua.

> Trước đây tool mặc định dùng `PostMessage`. Trên game hiện tại cách đó
> chỉ hoạt động với **một** cửa sổ đang có focus, nên các acc còn lại
> đứng yên mà tool vẫn báo "đã đổi khu". Đó là lỗi đã được sửa: mặc định
> quay lại giành focus.

> Ô chat được xoá bằng BACKSPACE có kiểm tra: tool chỉ xoá khi thấy chữ
> và dừng ngay khi ô trống. Không dùng `Ctrl+A` vì ô chat của game coi
> phím A là ký tự thường, gõ thành `ak 15` thay vì `k 15`. Nếu vẫn thấy
> `ak 15`, hãy tắt hết tool rồi chạy lại để nạp bản mới.

Muốn thử lại chế độ không giành focus:

```powershell
python boss_hunter.py --zones 0-25 --post-keys
```

## 6. Kiểm chứng detector

```powershell
python calibrate.py --watch
```

Hiện ảnh game kèm khung tím khoanh vùng tìm được. Dòng trên cùng ghi:

```
TIM THAY | Dang danh: CO | ratio: 0.356
```

- `TIM THAY` nghĩa là tìm được dòng chữ đỏ.
- `Dang danh: CO` nghĩa là đã xác nhận ổn định (giống hệt cách tool chạy).

Chạy khi đang ở khu có boss thì phải hiện `Dang danh: CO`.

### Chạy test không cần mở game

```powershell
python test_detect_offline.py   # bộ phát hiện: 13/13 PASS
python test_fight_flow.py       # vòng lặp đánh boss: 20/20 PASS
python test_zone_switch.py      # đổi khu có xác nhận: 25/25 PASS
python test_zone_plan.py        # map ít khu, quay vòng: 19/19 PASS
```

`test_zone_switch.py` kiểm tra đúng lỗi "acc vào nhầm khu": lệnh đổi khu bị
game chặn, lệnh bị nuốt, ô chat còn mở, acc đã đứng đúng khu từ trước, và
việc 26 khu cho ra 26 hình ảnh chỉ số khác nhau.

`test_zone_plan.py` kiểm tra map ít hơn 25 khu: chỉ thu hẹp dải sau khi lỗi
đổi khu được xác nhận nhiều lần, chia lại đủ khu cho các acc, và quay về khu
đầu khi hết vòng.

`test_detect_offline.py` kiểm tra việc nhận diện thanh máu bình thường,
thanh máu về 0, số sát thương, màn hình trống, và cả trường hợp số lớn
bị tách thành nhiều cụm.

`test_fight_flow.py` kiểm tra vòng lặp đánh boss: boss chết (thanh máu
biến mất), boss chết (máu về 0), cả đội bỏ khu, và acc tới khu muộn.

## 7. Tuỳ chỉnh

Mọi tham số nằm trong `config.json`:

```json
{
  "boss_hp_min_ratio": 0.25,
  "detect_stable_frames": 2,
  "detect_stable_interval": 0.3,
  "zone_switch_cooldown": 13.0,
  "zone_dwell": 3.0,
  "zone_missing_confirmations": 2,
  "attack_keys": ["ENTER"],
  "attack_burst": 5,
  "attack_interval": 0.12,
  "boss_gone_timeout": 15.0,
  "boss_dead_frames": 3,
  "boss_phase_gap": 10.0,
  "toggle_key": "F9",
  "post_keys": false
}
```

| Tham số | Ý nghĩa |
|---|---|
| `detect_stable_frames` | Số ảnh liên tiếp phải thấy boss mới xác nhận |
| `zone_switch_cooldown` | Giây tối thiểu giữa hai lần đổi khu (game báo chờ 9s) |
| `zone_dwell` | Giây chờ ở mỗi khu để nhân vật tới nơi |
| `zone_missing_confirmations` | Số lần `k X` không đổi khu trước khi coi X không tồn tại và thu hẹp dải khu (mặc định 2) |
| `attack_burst` | Số ENTER bấm liên tiếp trong một lần giành focus |
| `attack_interval` | Nghỉ giữa hai loạt ENTER |
| `boss_gone_timeout` | Giây không thấy boss thì coi như đã chết |
| `boss_dead_frames` | Số lần đọc liên tiếp không thấy thanh máu trước khi bắt đầu chờ phase mới |
| `boss_phase_gap` | Giây chờ boss hiện lại sau khi thanh máu mất/về 0 (mặc định 10, boss nhiều phase). Hết thời gian mới coi là chết |
| `post_keys` | `false` = giành focus khi gửi phím (mặc định, ổn định). `true` = gửi PostMessage (chỉ khi `--test-keys` báo game nhận) |

Nếu server có boss chuyển phase lâu hơn, tăng `boss_phase_gap` (giây). Đặt
`0` nếu muốn kết luận boss chết ngay sau `boss_dead_frames` như bản cũ.

## 8. File log

Mỗi acc ghi log riêng:

```
nro_boss.log        # log chung
nro_boss_acc0.log   # acc 0
nro_boss_acc1.log   # acc 1
```

## 9. Lưu ý

- **Chỉ chạy một cửa sổ tool tại một thời điểm.** Hai instance cùng gửi
  phím sẽ giành focus của nhau và cả hai đều chạy sai. Tool đã tự chặn:
  instance thứ hai sẽ báo lỗi và thoát ngay, không gõ phím nào.
- **Cả các acc phải ở cùng một map.** Tool chỉ đổi khu trong map hiện tại,
  không đổi map. Mỗi map có bộ khu riêng. (Lệnh `k X` của game có thể đưa
  sang map khác; tool nhận biết bằng chính dòng chỉ số khu, và học mỗi
  dòng một lần.)
- Boss có thể nằm ở một hàng cụ thể trong khu (cột `Hàng: -N` trong bảng
  `Boss (SL: xx)`). Hiện tại tool dò cả khu, nếu boss ở xa hàng thì cần thêm
  bước đi tới hàng — giai đoạn sau.
- Game giới hạn tần suất chuyển khu. Nếu thấy hộp thoại "không thể đổi khu
  vực" nhiều, tăng `zone_switch_cooldown`.
- Chạy game ở độ phân giải cố định, đừng đổi khi tool đang chạy.
- Tool chỉ hoạt động khi cửa sổ game không bị thu nhỏ.
