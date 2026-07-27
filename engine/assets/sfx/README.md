# SFX thư viện CapCut + mặc định Kenney

## Mặc định (đầu list)

| Key | Tên hiện thị | Nguồn |
| --- | --- | --- |
| `chi-tay` | Chỉ tay (trái / phải) | [Kenney Interface Sounds](https://kenney.nl/assets/interface-sounds) · `switch_004` (CC0) |
| `mo-hai-tay` | Mở hai tay | Kenney Interface Sounds · `open_002` (CC0) |

File gốc nằm ở `source-kenney/`.

## Thư viện đa dạng

Các sound còn lại lấy từ video CapCut (OCR tên gốc), xem `library.json` + `library/*.wav`.

Quy tắc mix: phát khi đổi pose; cooldown ~0.6s khi tua xuôi; chỉnh `sfxVolume` trong editor.

## Âm pose tham chiếu Reel 2056888022371492

Bốn âm ngắn tương đương chất pop/sweep của Reel được tải từ Mixkit và chuẩn hóa mono WAV 48 kHz:

- `mixkit-hard-pop-click.wav` — Hard pop click
- `mixkit-explainer-pop-whoosh.wav` — Explainer video pops whoosh light pop
- `mixkit-bubble-pop.wav` — Bubble pop up alert notification
- `mixkit-fast-small-sweep.wav` — Fast small sweep transition

Nguồn: [Mixkit Pop Sound Effects](https://mixkit.co/free-sound-effects/pop/) và [Mixkit Swoosh Sound Effects](https://mixkit.co/free-sound-effects/swoosh/), theo Mixkit License.
