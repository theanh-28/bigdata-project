# CHƯƠNG 3. KẾT QUẢ THỰC NGHIỆM MONGODB

> Lượt chạy sạch ngày 29/10/2026. Kết quả được tạo lại từ dữ liệu nguồn Yelp còn nguyên, sau khi lưu bộ kết quả cũ vào thư mục backup riêng, xóa container/volume/image MongoDB thuộc project và tạo lại môi trường. Latency là round-trip từ Python/PyMongo. P95/P99 là thống kê mô tả theo số lần lặp đã cấu hình.

## 3.1. Mục tiêu và kết luận giả thuyết

Thực nghiệm khảo sát Variety và Volume với MongoDB 8.0.34 trên một máy Windows/Docker. Đã chạy E1–E5; E6 và E7 chưa chạy. Kết quả chỉ áp dụng cho Yelp dataset và workload đã mô tả, không phải so sánh production hay MongoDB với PostgreSQL.

| Giả thuyết | Bằng chứng ở lượt chạy sạch | Kết luận |
|---|---|---|
| H1 — Variety | E1: 36/39 khóa thuộc tính xuất hiện ở dưới 50% business. E2: hai phiên bản document cùng tồn tại trong collection tách biệt, baseline không đổi. | Đạt theo tiêu chí đã đặt. |
| H2 — Index và working set | E3: ở 2M, Q1–Q3 nhanh hơn lần lượt khoảng 3.233×, 1.782× và 12,55× khi thêm index; Q4 chậm hơn khoảng 4,3%. E4 không thấy chênh p95 2× giữa các cache đã thử. | Đạt một phần: index hữu ích theo workload, nhưng không giúp mọi truy vấn; chưa quan sát được cache wall. |
| H3 — Thiết kế document | E6 chưa chạy. | Chưa thực nghiệm. |
| H4 — Sharding | E5a ghi range 100% vào một shard, hashed chia gần đều. E5b xác nhận truy vấn equality theo shard key được nhắm một shard. E5c có p50 sharded cao hơn 63,0% và throughput thấp hơn 38,0% trên máy đơn. | Đạt một phần về phân bố và định tuyến; phép đo E5c chưa cô lập topology do bộ index khác nhau và proxy dữ liệu/shard vượt cache. |

## 3.2. Môi trường và dữ liệu

- Windows 11; 8 logical CPU, 4 physical CPU; RAM host 15,77 GiB. Loại ổ lưu trữ không được ghi nhận.
- Docker 29.5.3, Compose 5.1.4; MongoDB 8.0.34; Python 3.12.13; PyMongo 4.19.0.
- Bộ nhớ Docker khả dụng khoảng 7,64 GiB. Standalone dùng giới hạn 1.500 MB cho E1–E3; E4 thay đổi cả WiredTiger cache và giới hạn container theo từng cấu hình.
- Yelp source JSONL được giữ nguyên. Nạp 150.346 business và tối đa 2.000.000 review vào lượt chạy mới.
- E5 dùng một config server, ba shard (mỗi shard là replica set một node) và một mongos, tất cả chạy trên cùng máy; không đại diện cho HA hoặc scale-out nhiều máy.
- Trước lượt chạy, kết quả cũ được sao lưu tại backups/full_rerun_20261029_144431_before_clean. Backup gồm 84 tệp, 71.424.814 byte; SHA-256 đã được đối chiếu.

## 3.3. E1 — Độ thưa thuộc tính

Trong 150.346 business, 136.602 document có attributes dạng object; 13.744 document không có object thuộc tính. Script quan sát K = 39 khóa có giá trị khác null. Có 36 khóa (92,31%) xuất hiện ở dưới 50% tổng số business; tỷ lệ ô null ước lượng trên bảng phẳng là 79,4181%.

Kết quả đáp ứng tiêu chí Variety của H1. Tỷ lệ null là ước lượng từ giá trị khác null quan sát được, không phải phép đo kích thước của một bảng quan hệ thực tế.

Biểu đồ: figures/e1_attribute_prevalence.png.

## 3.4. E2 — Hai phiên bản document

E2 dùng collection riêng reviews_e2, giữ nguyên reviews: chép 100.000 document v1 không có schema_version, sau đó chèn 100.000 document v2 có schema_version=2 và owner_reply. Tổng cộng 200.000 document, không có migration schema. Chép v1 mất 2,255 giây; chèn v2 mất 2,495 giây, tương ứng 40.075,16 document/giây. Truy vấn hỗn hợp theo business ID có p50 64,9086 ms trong 10 lượt.

Script dùng review_id mới có tiền tố v2- cho bản v2. Vì vậy phép thử chứng minh hai hình dạng document có thể cùng tồn tại và được truy vấn; nó không đo chuyển đổi tại chỗ cho cùng một thực thể, chi phí migration hay tính nhất quán giữa hai bản.

## 3.5. E3 — Quy mô và index

Các checkpoint 500k, 1M và 2M đều đạt đúng số document. Mỗi checkpoint có 3 vòng, 10 lần đo mỗi truy vấn trong mỗi vòng. Cấu hình no_index vẫn giữ index mặc định trên _id; with_index bổ sung business_id, user_id, date và review_id.

P50 của cấu hình có index:

| Số review | Q1 business_id (ms) | Q2 user_id (ms) | Q3 date theo tháng (ms) | Q4 group top 10 (ms) |
|---:|---:|---:|---:|---:|
| 500.000 | 1,1026 | 1,0572 | 13,9931 | 277,2975 |
| 1.000.000 | 1,2600 | 1,5313 | 76,6636 | 1.363,0201 |
| 2.000.000 | 1,1477 | 2,3660 | 342,4321 | 4.807,9286 |

Chi tiết tại checkpoint 2M:

| Query | P50 không index (ms) | P95 không index (ms) | P50 có index (ms) | P95 có index (ms) | docsExamined có index | Thay đổi p50 |
|---|---:|---:|---:|---:|---:|---:|
| Q1 — business_id | 3.710,2633 | 5.029,4596 | 1,1477 | 1,6821 | 19 | nhanh hơn khoảng 3.233× |
| Q2 — user_id | 4.215,8199 | 5.267,3000 | 2,3660 | 2,8558 | 121 | nhanh hơn khoảng 1.782× |
| Q3 — date theo tháng | 4.298,8678 | 5.411,5447 | 342,4321 | 476,3632 | 10.106 | nhanh hơn khoảng 12,55× |
| Q4 — group top 10 | 4.611,1630 | 5.459,7645 | 4.807,9286 | 5.706,7566 | 2.000.000 | chậm hơn khoảng 4,3% |

Q1–Q3 giảm số document cần xét mạnh so với quét 2 triệu document. Q4 vẫn xét toàn bộ collection nên bộ index này không giúp workload group top 10. Cấu hình có index ghi nhận collStats.totalIndexSize là 165.969.920 byte, gồm index _id_ (khoảng 158,28 MiB); tổng thời gian tạo các index bổ sung được ghi là 27,826 giây.

Ở mỗi vòng, thứ tự là no_index rồi with_index; mỗi query có một warm-up bị loại khỏi kết quả, nhưng cấu hình không được ngẫu nhiên hóa hoặc xen kẽ. Cache state có thể ưu tiên cấu hình chạy sau. P95/P99 có mẫu nhỏ nên chỉ mang tính mô tả.

Biểu đồ: figures/e3_Q1_business_id.png, figures/e3_Q2_user_id.png, figures/e3_Q3_date_month.png, figures/e3_Q4_group_top10.png.

## 3.6. E4 — WiredTiger cache

Ở 2M review, size logic + totalIndexSize cho proxy D ≈ 1,6419 GiB. Đây là proxy kích thước logic, không phải working set thực trong RAM. Mỗi cấu hình có 3 lượt cold sau khi restart container và 3 lượt hot sau 20.000 thao tác warm-up; mỗi lượt đo 50.000 lookup trên fixture cố định 60.000 ID. Tất cả lượt tìm thấy đủ khóa.

| Cache WiredTiger | P50 cold median (ms) | P95 cold median (ms) | P50 hot median (ms) | P95 hot median (ms) |
|---:|---:|---:|---:|---:|
| 0,5 GiB | 1,3503 | 2,21001 | 1,3435 | 2,17410 |
| 0,375 GiB | 1,3266 | 2,13350 | 1,3655 | 2,21260 |
| 0,256 GiB | 1,3294 | 2,15031 | 1,3628 | 2,19950 |

P95 hot lớn nhất so với nhỏ nhất chỉ khoảng 1,02×, dưới ngưỡng 2× đặt làm dấu hiệu hiệu ứng rõ. Không thấy cache wall trong phép đo này. “Cold” chỉ có nghĩa WiredTiger vừa khởi động lại; filesystem cache của Windows không bị xóa.

Cache WiredTiger và giới hạn memory container cùng thay đổi theo từng mức (0,5/1.500 MB, 0,375/1.250 MB, 0,256/1.100 MB), do đó không thể quy mọi khác biệt cho riêng WiredTiger cache. Warm-up chạy lại phần đầu fixture nên chỉ 20.000/50.000 khóa đầu được làm nóng trực tiếp trước lượt hot. Kích thước BSON/storage và biểu diễn trong bộ nhớ WiredTiger khác nhau; D không được diễn giải như dung lượng RAM thực tế. Tài liệu MongoDB mô tả cache WiredTiger và filesystem cache riêng biệt: https://www.mongodb.com/docs/v8.0/core/wiredtiger/.

Biểu đồ: figures/e4_cache_latency.png. Snapshot Docker stats ở mức cache nhỏ nhất: results/docker_stats_e4_min_cache.txt.

## 3.7. E5 — Sharding

Cụm E5 có một config server và ba shard replica set một node, cùng mongos, trên cùng host. CPU và ổ đĩa vẫn dùng chung.

### E5a — Phân bố insert

Ghi nhận chunk ban đầu trước khi dừng balancer để giữ placement cố định trong lượt nạp. reviews_range có một chunk ban đầu trên shard1; reviews_hashed_seq có ba chunk, mỗi shard một chunk. Cả hai nhận cùng 100.000 review theo seq tăng dần.

| Collection / shard key | Insert | Thời gian (s) | Tốc độ (doc/s) | Phân bố |
|---|---:|---:|---:|---|
| reviews_range, {seq: 1} | 100.000 | 3,286 | 30.435,77 | shard1 100%; shard2/3 0% |
| reviews_hashed_seq, {seq: hashed} | 100.000 | 3,085 | 32.418,06 | shard1 33,14%; shard2 33,10%; shard3 33,76% |

Ở checkpoint này, chuỗi range tăng dần được ghi vào chunk nằm trên shard1; hashed được chia gần đều do chunk hash đã nằm trên cả ba shard. Kết quả phụ thuộc placement đã đo, không chứng minh mọi cấu hình range luôn làm nóng một shard; split, migration và trạng thái balancer có thể đổi vị trí chunk. Tốc độ chỉ mang tính mô tả vì hai collection được nạp lần lượt trên cùng máy. Tham khảo tài liệu MongoDB về shard key và hashed sharding: https://www.mongodb.com/docs/v8.0/core/sharding-choose-a-shard-key/ và https://www.mongodb.com/docs/manual/core/hashed-sharding/.

### E5b — Targeted và scatter-gather

reviews_targeted có 100.000 document, shard key {review_id: hashed} và index phụ business_id. Mỗi mẫu query chạy 1.000 lượt.

| Query | Route/plan theo explain | docs/keys examined | P50 (ms) | P95 (ms) | P99 (ms) | Kết quả ở lượt cuối |
|---|---|---:|---:|---:|---:|---:|
| Exact review_id | SINGLE_SHARD; IXSCAN | 1 / 1 | 1,5873 | 2,7180 | 3,5147 | 1 |
| Exact business_id | SHARD_MERGE cả 3 shard; IXSCAN mỗi shard | 8 / 8 | 1,8727 | 2,8318 | 3,2647 | 8 |
| Khoảng review_id trên hashed key | SHARD_MERGE cả 3 shard; COLLSCAN | 603 / 0 | 2,1893 | 3,0838 | 3,4125 | 100 |

Explain xác nhận equality trên shard key nhắm một shard; query không có shard key phát tán đến cả cụm. Index business_id giúp từng shard dùng index nhưng không làm query targeted. Khoảng trên trường hashed không tận dụng được thứ tự của giá trị gốc và explain ghi nhận COLLSCAN tại từng shard. Ba query có selectivity/số kết quả khác nhau nên latency của chúng chỉ minh họa route/plan, không phải phép so sánh công bằng hiệu năng query.

### E5c — Standalone so với ba shard

Hai pha có đúng 2.000.000 document, cùng fixture 50.000 review_id (SHA-256: 7ec650a8590a2bb34463d7dbb65739da34400fee8dfd2af4347881b4c9fc6941), 20.000 warm-up và 3 vòng × 50.000 lookup. Mọi khóa fixture đều tồn tại ở cả hai collection. Collection sharded dùng {review_id: hashed}; số document trên shard1/2/3 là 666.107 / 668.532 / 665.361 (33,305% / 33,427% / 33,268%).

| Cấu hình | Median p50 (ms) | Median p95 (ms) | Median p99 (ms) | Median ops/s |
|---|---:|---:|---:|---:|
| Standalone | 1,3784 | 2,19651 | 2,64434 | 674,59 |
| Qua mongos, ba shard | 2,2468 | 3,29710 | 3,94903 | 418,04 |
| Thay đổi sharded so với standalone | +63,001% | +50,106% | +49,339% | -38,031% |

Trên một máy, point lookup qua mongos chậm hơn và throughput thấp hơn. Điều đó không phủ định lý thuyết sharding: lookup theo shard key chỉ tới một shard, còn mongos thêm bước định tuyến; một máy đơn cũng không cung cấp tài nguyên độc lập để kiểm tra scale-out.

Các hạn chế cần giữ khi diễn giải:

1. Proxy logic data + index toàn cụm là 1,6426 GiB. So với tổng cache 1,5 GiB, tỷ lệ proxy/tổng cache là khoảng 1,095×. Proxy lớn nhất trên một shard là 0,5503 GiB so với cache 0,5 GiB, tức 1,101×. Mục tiêu hướng dẫn cho điều kiện fit (không quá khoảng 0,9× cache của từng shard) không đạt; không dùng E5c để kết luận sharding giải quyết cache wall.
2. Bộ index hai pha khác nhau. Standalone có _id_, business_id_1, user_id_1, date_1, review_id_1; sharded có _id_ và review_id_hashed. Cả hai đều hỗ trợ tìm review_id, nhưng loại index và index phụ làm footprint khác nhau; phép đo không cô lập riêng tác động của topology.
3. Một host duy nhất, workload point lookup tuần tự qua PyMongo; latency bao gồm client round-trip, Docker network và mongos. P95/P99 là mô tả, không phải SLA hay kết quả production.

E5a hỗ trợ kết luận về placement của checkpoint; explain E5b hỗ trợ kết luận về định tuyến; E5c có cặp đo cùng quy mô/fixture/workload nhưng còn lệch bộ index và chưa đạt proxy cache mục tiêu. Vì vậy H4 chỉ đạt một phần.

Biểu đồ: figures/e5a_shard_insert_share.png và figures/e5c_single_vs_sharded.png.

## 3.8. Tổng kết

E1 xác nhận thuộc tính business thưa theo tiêu chí định trước. E2 cho thấy hai hình dạng document cùng tồn tại trong collection riêng mà không thay baseline. E3 cho thấy index cải thiện mạnh các truy vấn chọn lọc, nhưng Q4 group top 10 vẫn quét toàn bộ dữ liệu và chậm hơn một chút. E4 không phát hiện chênh latency 2× giữa ba mức cache. E5 cho thấy hashed placement gần đều và query equality theo shard key được nhắm một shard; E5c chậm hơn trên máy đơn và chưa đạt điều kiện proxy/cache để suy luận cache wall.

Kết quả áp dụng cho subset Yelp tối đa 2M review, một máy Windows/Docker và workload được định nghĩa trong scripts. Không suy rộng thành kết luận chung về MongoDB, PostgreSQL hay sharding production.

## Tệp kết quả

- E1/E2: results/e1_summary.json, results/e1_attribute_frequency.csv, results/e2_summary.json, results/e2_summary.csv.
- E3: results/e3_summary_500000.csv, results/e3_summary_1000000.csv, results/e3_summary_2000000.csv; raw CSV, rounds CSV và explain JSON tương ứng.
- E4: results/e4_summary.csv, results/e4_raw_*.csv, results/e4_cache_stats_*.json, results/e4_working_set_estimate.json, results/e4_query_keys.json.
- E5a/E5b: results/e5a_shard_distribution.csv, results/e5a_distribution_details.json, results/e5b_query_summary.csv, results/e5b_query_explain.json.
- E5c: fixture, rounds, raw CSV, metadata JSON, comparison CSV/JSON trong results/.
- Snapshot hậu kiểm Docker/balancer sau E1–E5: results/e5_post_run_snapshot.txt.
- Môi trường và nạp dữ liệu: results/environment.json, results/environment.csv, results/load_log.csv.
