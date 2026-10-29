# Bộ thực hành MongoDB cho đề tài Variety và Volume (E1-E5)

Bộ này chuyển quy trình Chương 3 thành các lệnh có thể chạy trên Windows 11 + Docker Desktop + Python. Script ghi số liệu vào CSV/JSON và vẽ biểu đồ từ các file kết quả. **Không có số liệu benchmark mẫu được giả làm số đo thật.**

## 0. Phạm vi và nguyên tắc

- **Phần bắt buộc:** E1 (độ đa dạng thuộc tính), E2 (thêm phiên bản document), E3 (quy mô và index), E4 (WiredTiger cache).
- **Phần mở rộng đáng làm nếu máy đủ RAM:** E5a (phân bố ghi theo shard key), E5b (targeted/scatter-gather) và E5c (so latency nút đơn với cụm 3 shard). E5c có script riêng; quy trình đo được mô tả tại mục 10.4.
- **Có thể bỏ nếu thiếu thời gian:** E6 (nhúng/tham chiếu/subset) và E7 (MongoDB so với PostgreSQL JSONB). Nếu bỏ, ghi rõ *chưa thực nghiệm*, không tuyên bố đã xác nhận.
- E1–E4 dùng **một MongoDB standalone**. E5 dùng một cụm thử nghiệm khác qua `mongos`. Không chạy hai bộ cùng lúc vì tranh chấp CPU/RAM/port.
- Ưu tiên Yelp Open Dataset nếu đã tải và được phép sử dụng cho bài học thuật/phi thương mại theo điều khoản hiện hành. Nếu không, dùng script sinh dữ liệu mô phỏng; khi đó phải ghi rõ dữ liệu mô phỏng, không gọi là Yelp.
- Các tiêu chí H1–H4 được đặt trước để tránh nhìn số liệu rồi mới đổi tiêu chí. Nếu kết quả không đạt, báo cáo kết quả thực và lý giải, không sửa số.

## 1. Cấu trúc thư mục được đưa lên GitHub

    mongodb_bigdata_lab/
    ├── README.md
    ├── compose/
    │   ├── docker-compose.single.yml
    │   └── docker-compose.shard.yml
    ├── scripts/                 # mã nguồn chạy E1–E5, nạp dữ liệu, khởi tạo cụm, vẽ biểu đồ
    ├── results/                 # kết quả mới nhất E1–E5, gồm CSV/JSON, log và snapshot
    ├── figures/                 # biểu đồ từ lượt chạy mới nhất
    ├── report/
    │   └── CHUONG3_KET_QUA.md   # báo cáo thực nghiệm mới nhất
    ├── .env.example
    ├── .gitignore
    ├── .python-version
    ├── pyproject.toml
    ├── requirements.txt
    └── uv.lock

Dữ liệu Yelp gốc, file .env, môi trường .venv, backup, gói deliverables, template và hướng dẫn cá nhân không được đưa lên repository. Tạo data/raw/ trên máy rồi đặt các file JSONL Yelp vào đó trước khi chạy lại.

## 2. Chuẩn bị trên Windows

### 2.1. Công cụ

1. Cài Docker Desktop, bật WSL 2 backend và khởi động Docker Desktop.
2. Cài `uv` cho Windows bằng PowerShell:

   ```powershell
   winget install --id=astral-sh.uv -e
   ```

   Nếu vừa cài xong, đóng và mở lại PowerShell rồi kiểm tra `uv --version`. Có thể dùng trình cài chính thức nếu máy không có WinGet: [Hướng dẫn cài uv](https://docs.astral.sh/uv/getting-started/installation/).
3. Tại thư mục gốc `mongodb_bigdata_lab`, chạy:

   ```powershell
   if (-not (Test-Path .env)) { Copy-Item .env.example .env } else { Write-Host ".env đã tồn tại; giữ cấu hình hiện tại, không sao chép đè." }
   uv python install 3.12
   uv sync
   uv run python --version
   ```

`pyproject.toml` khai báo các thư viện Python. Lần đầu chạy `uv sync`, `uv` tạo môi trường `.venv` và tạo/cập nhật `uv.lock` nếu cần; hãy giữ `uv.lock` sau khi dependency được phân giải để các lần chạy sau tái lập tốt hơn. Dùng `uv run python ...` cho các script. Không cần chạy `pip install` thủ công và không ghi đè `.env` nếu bạn đã cấu hình nó.

### 2.2. Kiểm tra Docker

```powershell
docker --version
docker compose version
docker info
```

Trong Docker Desktop > Settings > Resources, nên cấp tổng RAM cho Docker khoảng 6–8 GB nếu thực hiện E5. Nếu máy chỉ có 8 GB RAM, không nên cố chạy 3 shard; làm E1–E4 trước, dùng quy mô nhỏ hơn.

### 2.3. Ghi cấu hình máy

Khởi động MongoDB theo mục 4 rồi chạy:

```powershell
uv run python scripts/collect_environment.py --disk-type "SSD"
```

Thay `SSD` bằng loại ổ thật. File `results/environment.json` lưu hệ điều hành, CPU, RAM host, phiên bản Python/PyMongo/Docker/MongoDB. RAM Docker/container không đồng nghĩa RAM vật lý máy; chụp thêm `docker stats --no-stream` khi đo E4/E5.

## 3. Chọn dữ liệu

### Phương án A — Yelp Open Dataset (nên dùng nếu đã tải được)

Tải theo hướng dẫn tại https://www.yelp.com/dataset và chỉ giải nén hai file JSON Lines cần thiết. Đặt chúng vào:

```text
data/raw/yelp_academic_dataset_business.json
data/raw/yelp_academic_dataset_review.json
```

Mỗi dòng phải là một JSON object. Không cần tạo file cắt 2 triệu dòng; `load_data.py` đọc theo `--start` và `--limit`, tránh phải nhân đôi file vài GB. Không đưa dữ liệu gốc Yelp vào Git/repo/bộ nộp.

### Phương án B — Dữ liệu mô phỏng (dự phòng)

Nếu chưa có Yelp, sinh 20.000 business và 500.000 review để thử nhanh:

```powershell
uv run python scripts/generate_synthetic.py --businesses 20000 --reviews 500000 --seed 42
```

Để đủ mốc E3 2M, sinh 2 triệu review (cần nhiều dung lượng và thời gian hơn):

```powershell
uv run python scripts/generate_synthetic.py --businesses 50000 --reviews 2000000 --seed 42
```

Dữ liệu được tạo dưới `data/generated/`. Trong các lệnh sau, chọn đúng đường dẫn dữ liệu đang dùng. Ví dụ Yelp:

```powershell
$BusinessFile = "data/raw/yelp_academic_dataset_business.json"
$ReviewFile = "data/raw/yelp_academic_dataset_review.json"
```

Ví dụ mô phỏng:

```powershell
$BusinessFile = "data/generated/businesses.jsonl"
$ReviewFile = "data/generated/reviews.jsonl"
```

**Không trộn hai nguồn trong cùng một thí nghiệm.** Nếu đổi nguồn, xóa file fixture E3 cũ `results/e3_query_fixtures.json` trước khi chạy lại để tạo tập khóa phù hợp với nguồn mới.

## 4. Khởi động MongoDB standalone (E1-E4)

Tại thư mục gốc của project:

```powershell
docker compose --env-file .env -f compose/docker-compose.single.yml up -d
docker ps
docker exec -it mongo-exp mongosh --quiet --eval "db.version()"
```

Kiểm tra kết nối Python:

```powershell
uv run python -c "from pymongo import MongoClient; c=MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=5000); print(c.admin.command('ping')); c.close()"
```

MongoDB Compass có thể kết nối `mongodb://localhost:27017`. Các port được bind vào `127.0.0.1` để không chủ động expose database không xác thực ra mạng LAN. Volume `mongo_data` giữ dữ liệu khi chạy `docker compose down`; **không dùng `down -v`** trước khi hoàn tất vì lệnh đó xóa volume dữ liệu.

## 5. Nạp dữ liệu baseline

### 5.1. Nạp business cho E1

Chọn limit phù hợp: Yelp thường có khoảng 150k business, nhưng với dữ liệu khác phải xem số dòng thực tế. Với Yelp:

```powershell
uv run python scripts/load_data.py --file $BusinessFile --collection business --entity business --start 0 --limit 200000 --batch-size 5000
```

Lệnh nạp tối đa 200.000 dòng; nếu file ít hơn, script nạp đến hết file. Không dùng `--reset` trừ khi thực sự muốn xóa collection đích. Đếm kiểm tra:

```powershell
docker exec -it mongo-exp mongosh --quiet --eval "db.getSiblingDB('yelp').business.countDocuments({})"
```

### 5.2. Nạp baseline 500k review

```powershell
uv run python scripts/load_data.py --file $ReviewFile --collection reviews --entity reviews --start 0 --limit 500000 --batch-size 5000
```

`seq` được gán bằng số dòng nguồn bắt đầu từ 0, chỉ ở document review; đây là khóa tăng dần dùng cho E5. Không thêm `seq` vào business. Nếu vừa chạy lỗi giữa chừng, **đừng chạy lại cùng chunk trên collection đã có dữ liệu**: kiểm tra `results/load_log.csv`, đếm document và quyết định xóa/reload collection trong môi trường sạch.

### 5.3. Lệnh kiểm tra nhanh

```powershell
docker exec -it mongo-exp mongosh --quiet --eval "const d=db.getSiblingDB('yelp'); print('reviews='+d.reviews.countDocuments({})); printjson(d.reviews.findOne({}, {_id:0, review_id:1, business_id:1, user_id:1, date:1, seq:1})); printjson(d.reviews.stats())"
```

Nếu Yelp thực tế có trường `date` là chuỗi ISO `YYYY-MM-DD`, script E3 so sánh theo chuỗi. Nếu trường có kiểu khác, hãy điều chỉnh bộ lọc tháng và ghi rõ trong phương pháp; không chuyển ngầm dữ liệu rồi coi là cùng workload.

## 6. E1 — Độ đa dạng thuộc tính (Variety)

```powershell
uv run python scripts/e1_variety.py --collection business
```

Kết quả:

- `results/e1_summary.json`: số business, số document có `attributes` kiểu object, K khóa khác nhau, tỷ lệ ô NULL ước lượng.
- `results/e1_attribute_frequency.csv`: số lần xuất hiện và tỷ lệ của từng khóa.
- `figures/e1_attribute_prevalence.png`: top 15 thuộc tính theo tỷ lệ xuất hiện.

Cách đọc: `attributes` được chuyển thành bảng phẳng với K cột; ô không có giá trị khác null được xem là ô thiếu. Script coi khóa có giá trị tường minh `null` là ô thiếu khi tính độ thưa. Chỉ kết luận “độ thưa cao” khi CSV thực tế hỗ trợ điều đó.

Ảnh nên chụp cho báo cáo: collection `business`, document mẫu có/nhiều thuộc tính, và hình biểu đồ E1.

## 7. E2 — Thêm phiên bản document không sửa baseline

Yêu cầu source `reviews` có ít nhất 100k document. Chạy:

```powershell
uv run python scripts/e2_schema_evolution.py --source-collection reviews --target-collection reviews_e2 --n 100000 --rounds 10
```

Script tạo collection thử nghiệm `reviews_e2` riêng: 100k bản v1 không có `schema_version` và 100k bản v2 có `schema_version=2`, `owner_reply`. Nó **không chèn thêm 100k vào `reviews`**, nên mốc E3 vẫn chính xác. Nếu chạy lại, chỉ collection thử nghiệm `reviews_e2` bị tạo lại.

Kết quả: `results/e2_summary.json` và `results/e2_summary.csv`. Xác nhận version count trong file kết quả và kiểm tra `reviews` vẫn đúng 500k. Với báo cáo, không nói MongoDB nhanh hơn PostgreSQL chỉ vì thao tác thêm trường: PostgreSQL 11 trở lên tối ưu trường hợp thêm cột có default hằng số; lợi thế cần phân tích ở sự linh hoạt document/migration và quy trình ứng dụng.

## 8. E3 — Đo tăng trưởng và chi phí index

### Nguyên tắc workload

- Q1: đọc theo `business_id`.
- Q2: đọc theo `user_id`.
- Q3: đọc theo một khoảng tháng của `date`.
- Q4: aggregate toàn collection để đếm và tính điểm trung bình theo business, sắp xếp top 10.
- Mỗi truy vấn đọc hết kết quả phù hợp, không dùng `limit` cho Q1–Q3; như vậy việc bỏ index có thể làm MongoDB quét cả collection và thời gian gồm cả truyền kết quả về client.
- Script dùng tập tham số cố định được lưu trong `results/e3_query_fixtures.json` để các cấu hình so sánh cùng điều kiện.

### Mốc 500k

```powershell
uv run python scripts/e3_index_scaling.py --n-docs 500000 --rounds 3 --repeats 10
```

Đo xong, script để lại các secondary index. Tiếp tục nạp chunk thứ hai:

```powershell
uv run python scripts/load_data.py --file $ReviewFile --collection reviews --entity reviews --start 500000 --limit 500000 --batch-size 5000
uv run python scripts/e3_index_scaling.py --n-docs 1000000 --rounds 3 --repeats 10
```

Nạp tiếp lên 2M rồi chạy:

```powershell
uv run python scripts/load_data.py --file $ReviewFile --collection reviews --entity reviews --start 1000000 --limit 1000000 --batch-size 5000
uv run python scripts/e3_index_scaling.py --n-docs 2000000 --rounds 3 --repeats 10
```

Nếu máy chậm, chạy pilot `--rounds 1 --repeats 3` trước; số liệu đưa vào báo cáo cuối nên theo một quy tắc thống nhất, mặc định 3 vòng và 10 lần chạy mỗi truy vấn trong mỗi vòng. p95 tính từ 10 lần mỗi vòng chỉ nên diễn giải mô tả, không phải ước lượng tail latency có độ tin cậy cao. Mỗi lần chạy E3 phải có count bằng đúng `--n-docs`; script dừng nếu có dư document (ví dụ E2 bị chạy nhầm vào `reviews`).

Kết quả: `results/e3_raw_<N>.csv`, `results/e3_rounds_<N>.csv`, `results/e3_summary_<N>.csv` (trung vị qua 3 vòng), `results/e3_explain_<N>.json`, `results/e3_query_fixtures.json`. Các cột quan trọng: p50, p95, p99, docsExamined, keysExamined, index size, thời gian tạo index. `plot_results.py` tổng hợp các mốc đã đo để vẽ hình so sánh.

**Lưu ý cách diễn giải:** Q4 phải group nhiều document nên có thể vẫn quét phần lớn dữ liệu dù có các index này. Không ghi kết luận “index luôn làm truy vấn nhanh hơn”; chỉ báo đúng những truy vấn có index được dùng theo `explain`.

## 9. E4 — Kiểm tra hiệu ứng WiredTiger cache

### 9.1. Ước lượng D và tạo tập khóa cố định

Sau khi `reviews` có 2M document, chạy:

```powershell
uv run python scripts/e4_cache.py --mode stats
uv run python scripts/e4_cache.py --mode make-keys --key-count 60000
```

`results/e4_working_set_estimate.json` ghi `D ≈ size + totalIndexSize`. Đây chỉ là ước lượng thô: biểu diễn trong RAM khác kích thước BSON trên đĩa, và filesystem cache cũng có thể giữ trang dữ liệu. Không mặc định D bằng chính xác working set trong RAM.

### 9.2. Chọn C hợp lý

Tài liệu MongoDB 8.0 nêu giới hạn WiredTiger cache cấu hình từ **0,256 GB** trở lên. Cache phải nhỏ hơn lượng RAM container, đồng thời container cần RAM cho process và bộ nhớ ngoài cache. Ví dụ thử:

| C | `WT_CACHE_GB` | `MONGO_MEMORY_LIMIT` |
|---|---:|---:|
| C1 | 0.5 | 1500m |
| C2 | 0.375 | 1250m |
| C3 | 0.256 | 1100m |

Đây là bộ mức thử ví dụ, không phải bắt buộc. Chọn các mức khả thi dựa trên D, RAM máy và `docker stats`. Nếu C = D không thể cấp trên laptop, ghi giới hạn đó và chọn các mức nhỏ hơn; không giả vờ đã chạy một cấu hình không thể cấp.

### 9.3. Đổi cache, nhưng giữ data volume

1. Sửa `.env` ở project root, ví dụ `WT_CACHE_GB=0.5` và `MONGO_MEMORY_LIMIT=1500m`.
2. Recreate container để tham số cache có hiệu lực:

```powershell
docker compose --env-file .env -f compose/docker-compose.single.yml down
docker compose --env-file .env -f compose/docker-compose.single.yml up -d
Start-Sleep -Seconds 8
docker exec -it mongo-exp mongosh --quiet --eval "db.version()"
```

`down` không xóa volume dữ liệu. Không dùng `down -v`.
3. Lượt cache WiredTiger mới ngay sau khi container khởi động (không warm-up trước):

```powershell
uv run python scripts/e4_cache.py --mode run --label C0_5_cold --skip-warmup --ops 50000 --round 1
```

4. Lượt nóng trên cùng cấu hình, loại 20k thao tác warm-up khỏi số đo:

```powershell
uv run python scripts/e4_cache.py --mode run --label C0_5_hot --warmup 20000 --ops 50000 --round 1
```

5. Lặp lại 3 lượt cho từng mức C. Để lượt “cold” có ý nghĩa, cần recreate container trước lượt đó; lưu ý Windows/host filesystem cache không tự động bị xóa, vì vậy gọi đó là cache WiredTiger mới khởi động, không khẳng định toàn bộ hệ thống ở trạng thái cold tuyệt đối. Ghi `docker stats --no-stream` sau khi đo.

Pilot nhanh có thể dùng `--ops 5000 --warmup 2000`; nhưng ghi rõ pilot và không gộp với lượt 50k trong cùng bảng tổng hợp mà không phân biệt.

Kết quả: `results/e4_summary.csv`, `results/e4_raw_<label>_r<round>.csv`, `results/e4_cache_stats_*.json`. Sau khi đã thu đủ cấu hình, chạy `plot_results.py` để vẽ `figures/e4_cache_latency.png`.

**Nếu không thấy điểm gãy:** có thể vì data nhỏ, SSD/OS filesystem cache che hiệu ứng, working set ước lượng chưa đúng hoặc các mức C quá gần nhau. Ghi đúng số đo; kết quả không như kỳ vọng vẫn hợp lệ.

## 10. E5 — Sharding (mở rộng; cần RAM và thời gian)

**Nếu định làm E5c, hãy thực hiện mục 10.4 trước mục 10.1–10.3** để đo baseline standalone trước khi chuyển sang mongos. Nếu bỏ E5c, cứ theo thứ tự E5a/E5b bên dưới.

### 10.1. Chuyển standalone sang sharded cluster

Đảm bảo đã lưu kết quả E1–E4. Dừng container standalone **không xóa volume**:

```powershell
docker compose --env-file .env -f compose/docker-compose.single.yml down
```

Khởi tạo config server, ba replica set một thành viên, rồi mongos:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/init_sharded.ps1
```

Nếu script báo lỗi, xem log bằng `docker compose --env-file .env -f compose/docker-compose.shard.yml logs --tail 100 cfg1 shard1 shard2 shard3 mongos`. Kiểm tra mỗi replica set có PRIMARY và `sh.status()` liệt kê 3 shard. Cụm thực hành mỗi shard chỉ có một thành viên, **không có tính sẵn sàng cao như triển khai production**.

Tạo collection shard rỗng trước khi nạp dữ liệu:

```powershell
docker compose --env-file .env -f compose/docker-compose.shard.yml exec -T mongos mongosh --quiet --file /scripts/setup_sharded_collections.js
```

Compose đã mount thư mục `scripts` read-only vào `/scripts` bên trong `mongos`; script cũng tạo index phụ `business_id` cho phép thử scatter-gather. Sau khi tạo collection, kiểm tra trạng thái chunk/shard bằng `sh.status()`. Với MongoDB 8.0, hashed sharding collection rỗng có cơ chế tạo/phân phối chunk ban đầu khác range sharding; chờ quá trình tạo chunk ban đầu hoàn tất, xác nhận chunk placement rồi mới tùy chọn dừng balancer để giữ cấu hình trong pha đo:

```powershell
docker compose --env-file .env -f compose/docker-compose.shard.yml exec -T mongos mongosh --quiet --eval "sh.stopBalancer(); printjson(sh.status())"
```

Không dừng balancer trước khi xác nhận phân phối chunk ban đầu. Không ghi rằng phân phối chắc chắn đều nếu trạng thái thực tế không xác nhận.

### 10.2. E5a — Range key so với hashed key

Dùng 100k document cho lần đầu để giảm chi phí; tăng lên 300k nếu máy chạy ổn. Chạy qua `mongos` ở `mongodb://localhost:27017`:

```powershell
uv run python scripts/e5_shard_distribution.py --file $ReviewFile --start 0 --limit 100000 --batch-size 2000
```

Script nạp cùng một dải document vào `reviews_range` shard theo `{seq: 1}` và `reviews_hashed_seq` shard theo `{seq: 'hashed'}`; đo hiệu số `opcounters.insert` trực tiếp trên ba shard trước/sau từng pha. Kết quả: `results/e5a_shard_distribution.csv`, `results/e5a_distribution_details.json`. CSV giữ cả `opcounters.insert` delta và số document/ tỷ lệ document theo `collStats`; dùng số document theo shard làm bằng chứng phân phối chính, còn op-counter delta là chỉ số hoạt động bổ sung vì nó đếm operation/command chứ không nhất thiết là từng document.

Đọc kết quả cùng `sh.status()`/chunk distribution. Thí nghiệm có thể bị tác động bởi cách tạo chunk, balancer, kích thước dữ liệu và phiên bản MongoDB. Nếu bật/tắt balancer giữa các lần chạy, phải ghi rõ. Không lấy `opcounters.insert` tuyệt đối làm số liệu workload; dùng **delta trước/sau pha nạp**.

### 10.3. E5b — Targeted và scatter-gather

Nạp collection riêng đã được shard theo `review_id: hashed`:

```powershell
uv run python scripts/load_data.py --file $ReviewFile --collection reviews_targeted --entity reviews --start 0 --limit 100000 --batch-size 2000
```

Sau đó benchmark explain và latency:

```powershell
uv run python scripts/e5_shard_queries.py --collection reviews_targeted --ops 1000
```

Kết quả: `results/e5b_query_summary.csv`, `results/e5b_query_explain.json`. So sánh query chính xác theo `review_id`, query theo `business_id` không phải shard key, và query theo khoảng trên `review_id` hashed. Explain có thể thay đổi tên stage theo phiên bản; đọc số shard/nhánh `shards`, docs/keys examined và filter thực tế, không phụ thuộc duy nhất vào tên `SINGLE_SHARD`/`SHARD_MERGE`.

### 10.4. E5c — Nút đơn so với 3 shard (tùy chọn; đã có script)

Thực hiện tuần tự: lưu fixture khi standalone đang có baseline, đo standalone, khởi động cụm sharding, tạo và nạp collection E5c bằng script setup, đo sharded rồi tổng hợp. Các lệnh đo nằm ngay dưới đây.

Script `scripts/e5c_single_vs_sharded.py` có ba chế độ:

```powershell
# Khi standalone đang chạy và yelp.reviews có đúng N document
uv run python scripts/e5c_single_vs_sharded.py --mode capture-keys --n-docs 2000000 --key-count 50000 --seed 42
uv run python scripts/e5c_single_vs_sharded.py --mode measure --role single --collection reviews --n-docs 2000000 --rounds 3 --ops 50000 --warmup 20000

# Sau khi chuyển sang cụm sharding, tạo collection và nạp cùng 2M review theo quy trình tại mục 10.4
uv run python scripts/e5c_single_vs_sharded.py --mode measure --role sharded --collection reviews_e5c --n-docs 2000000 --rounds 3 --ops 50000 --warmup 20000
uv run python scripts/e5c_single_vs_sharded.py --mode summarize
```

Thay `2000000` bằng checkpoint N chính xác thực sự đã nạp ở cả hai cấu hình. Không tạo lại `results/e5c_query_keys.json` giữa hai pha. Script lưu raw CSV, CSV theo vòng, metadata cache/index/phân phối shard và CSV so sánh. Nếu `D/C` chưa đủ lớn hoặc phân phối dữ liệu chưa đạt điều kiện, ghi kết quả là thăm dò/chưa xác nhận cache wall.

Cụm ba shard trên cùng laptop vẫn dùng chung CPU và ổ đĩa; E5c không chứng minh throughput production. E5c nên chạy **trước E5a/E5b** để đo standalone khi baseline còn dễ truy cập, nhưng hướng dẫn cũng nêu cách chuyển lại nếu E5a/E5b đã được chạy.

## 11. Vẽ lại biểu đồ và đóng gói kết quả

Sau khi hoàn tất các thí nghiệm đã chọn (bao gồm E5c nếu làm):

```powershell
uv run python scripts/plot_results.py
```

Chỉ các hình có CSV nguồn tương ứng mới được tạo. Kết quả hiện tại nằm trong results/, biểu đồ trong figures/ và báo cáo mới nhất trong report/CHUONG3_KET_QUA.md. Khi chạy lại, cập nhật báo cáo từ CSV/JSON thực tế; có thể chụp db.version(), db.reviews.stats(), explain, WiredTiger cache, docker stats và sh.status() để lưu minh chứng.

Trước khi nộp:

- Giữ script, Docker Compose, CSV, JSON giải thích và hình biểu đồ.
- Không nộp file JSONL Yelp hoặc bộ dữ liệu mô phỏng lớn.
- Không để số liệu minh họa/placeholder thành kết quả thực nghiệm.
- Nêu rõ hệ thống được chạy trên một máy cá nhân.
- Ghi phân công: ai thiết lập Docker, ai chạy thí nghiệm, ai phân tích/viết báo cáo, ai kiểm tra và ghép tài liệu.

## 12. Tiêu chí giả thuyết H1–H4 (chốt trước khi xem kết quả)

| Mã | Nội dung | Tiêu chí định lượng/định tính đề xuất | Thí nghiệm |
|---|---|---|---|
| H1 | MongoDB nhận dữ liệu khác cấu trúc mà không cần định nghĩa schema trước | Không có lỗi nạp; E1 có ít nhất 50% khóa thuộc tính xuất hiện ở dưới 50% tổng business; E2 v1/v2 tồn tại và truy vấn hỗn hợp chạy đúng | E1, E2 |
| H2 | Index cải thiện truy vấn chọn lọc; working set vượt cache có thể làm tăng độ trễ | Ở 2M, so p50/p95 Q1-Q3; “tăng ít nhất 10 lần” là ngưỡng xác nhận mạnh nếu đạt, nhưng báo cáo theo từng query. E4 xem p95 mức C nhỏ nhất so mức lớn nhất, ví dụ tăng ≥2 lần được coi là hiệu ứng rõ | E3, E4 |
| H3 | Nhúng/tham chiếu/subset đánh đổi độ trễ đọc, kích thước document và chi phí ghi | Nếu không chạy E6, đánh dấu “chưa kiểm chứng”, không đổi tên H3 để giả vờ đã thực nghiệm | E6 tùy chọn |
| H4 | Shard key ảnh hưởng hướng phân phối ghi và khả năng target query | So insert-op delta/chunk thật; query có giá trị shard key cần target ít shard hơn workload không có shard key. Chỉ kết luận sharding giúp vượt bức tường cache nếu chạy E5c với workload tương đương | E5a, E5b, E5c tùy chọn |

Đây là tiêu chí của bài lab, không phải cam kết trước về kết quả. Nếu chưa chạy E6, H3 phải ghi “chưa thực nghiệm”.

## 13. Xử lý lỗi nhanh

Khi gặp lỗi, kiểm tra log container và trạng thái replica set. Có thể tạo gói chẩn đoán bằng powershell -ExecutionPolicy Bypass -File scripts/collect_diagnostics.ps1; script loại trừ file .env và dữ liệu Yelp gốc.

**Port 27017 đã được dùng:** dừng compose trước đó (`docker compose --env-file .env -f compose/docker-compose.single.yml down`) trước khi bật mongos. Không chạy standalone và mongos đồng thời trên cùng port.

**Lỗi `coll.stats()` trong E3/E4:** PyMongo không có `Collection.stats()`; bản hiện tại dùng `db.command({"collStats": coll.name, "scale": 1})`. Nếu thấy lỗi cũ, đối chiếu script với phiên bản trong repository và ghi rõ thay đổi trước khi chạy lại.

**MongoDB không khởi động do cache/RAM:** giảm `WT_CACHE_GB` nhưng không dưới 0.256; tăng `MONGO_MEMORY_LIMIT` nếu host còn RAM. Kiểm tra `docker logs mongo-exp`.

**E3 báo checkpoint mismatch:** kiểm tra `countDocuments({})`; có thể đã chạy lại một chunk hoặc nạp E2 nhầm vào `reviews`. Không bỏ qua cảnh báo; phải reset baseline và chạy lại theo các chunk có `start` đúng.

**Không còn ổ đĩa:** kiểm tra `docker system df` và dung lượng volume. Không xóa volume dữ liệu trước khi sao lưu CSV/JSON. Chỉ xóa raw source sau khi xác nhận có thể tải lại và đã hoàn thành phần thực nghiệm cần thiết.

**E5 không khởi tạo:** kiểm tra log từng container, chờ replica set có PRIMARY, sau đó mới chạy `mongos`/`addShard`. Thử kết nối qua mongos bằng `docker compose --env-file .env -f compose/docker-compose.shard.yml exec -T mongos mongosh --quiet --eval "sh.status()"`.

**E4 không có điểm gãy:** báo cáo đúng. WiredTiger cache không phải toàn bộ working set; filesystem cache của hệ điều hành vẫn có thể giữ file data.

## 14. Kiểm tra và trạng thái kết quả hiện có

- E1–E5 đã được chạy end-to-end ngày 29/10/2026 trên Windows 11, Docker Desktop và MongoDB 8.0.34.
- results/ và figures/ chứa kết quả cùng biểu đồ của lượt chạy sạch mới nhất; report/CHUONG3_KET_QUA.md là báo cáo tương ứng.
- E6/E7 chưa chạy. Loại ổ lưu trữ của host chưa được ghi nhận.
- scripts/init_sharded.ps1 khởi tạo replica set, chờ primary và chỉ thêm shard còn thiếu; lệnh Compose truyền rõ --env-file .env.
- Không đưa Yelp JSONL, .env, backup hay hướng dẫn cá nhân vào GitHub.

## 15. Nguồn kỹ thuật để trích dẫn báo cáo

- Yelp Open Dataset và điều khoản sử dụng: https://www.yelp.com/dataset
- MongoDB 8.0, WiredTiger cache và memory limit: https://www.mongodb.com/docs/v8.0/faq/storage/
- MongoDB, hashed sharding và initial chunk distribution: https://www.mongodb.com/docs/manual/core/hashed-sharding/
- MongoDB, sharded-cluster components: https://www.mongodb.com/docs/manual/core/sharded-cluster-components/
- MongoDB 8.0, giải thích `executionStats`, `totalDocsExamined`, `totalKeysExamined`: https://www.mongodb.com/docs/v8.0/reference/explain-results/
- PostgreSQL 11, thêm cột với default hằng số: https://www.postgresql.org/docs/11/ddl-alter.html
- YCSB: https://github.com/brianfrankcooper/YCSB
