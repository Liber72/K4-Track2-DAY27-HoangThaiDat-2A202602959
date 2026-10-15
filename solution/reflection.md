# Reflection — Data Siege

**Học viên:** Hoàng Thái Đạt — 2A202602959

**Loại lỗi khó phát hiện nhất và lý do**

Lỗi nhẹ khó phân biệt với dao động sạch. Nếu cập nhật tâm phân phối từ luồng
đang kiểm tra, các lỗi nhỏ kéo dài có thể trở thành mức "bình thường" mới. Tôi
hiệu chỉnh tham chiếu từ các chỉ số practice sạch, ràng buộc bởi ngưỡng công bố
mean + 3 sigma, rồi giữ cố định tâm này. Ngưỡng được chọn riêng cho từng nhóm
kiểm tra bằng mô phỏng có tập kiểm định tách biệt.

Với feature, tâm tham chiếu là training mean; tôi không dịch tâm theo serving.
Thang nhiễu được ước lượng có giới hạn từ phía sai lệch ít bị ảnh hưởng hơn.
Cách này tránh hấp thụ skew kéo dài vào tham chiếu. Cả hai hướng sai lệch vẫn
được kiểm tra, với hiệu chỉnh hướng có giới hạn từ các lỗi practice. Freshness
hợp đồng được đối chiếu SLA khai báo và giới hạn sạch công bố, giảm cảnh báo
chỉ vì một dao động thống kê nhỏ.

Lineage còn khó ở chỗ khai báo `inputs` không nhất thiết liệt kê mọi dependency.
Tôi học dependency đã xuất hiện ít nhất hai lần trong cùng job, tránh bỏ sót
khi các mẫu đầu đã thiếu cạnh. Một cạnh mới xuất hiện một lần chưa trở thành
bắt buộc. Cách này vẫn có hạn chế khi pipeline thay đổi hợp lệ.

**Đánh đổi chi phí/khả năng phát hiện và hướng cải thiện**

Mỗi sự kiện gọi một công cụ phù hợp, có cache để tránh trả phí lặp. Tôi giữ độ
phủ khi ngân sách cạn. Sau khi xác nhận lớp cho phép chạy lại, điểm private tăng
từ 27,78 qua 35,81 lên 36,12. Bản cuối đạt practice 44,14 và public 40,99.
Public bắt đủ lỗi nhưng báo nhầm 23,97%; chi phí là 240/220 credits. Tệp kết quả
private thực tế còn có chỉ số tổng hợp: bắt lỗi 87,04%, báo nhầm 24,66% và
cost_overage bằng 0. Tôi thử thêm kiểm tra SLA, ngưỡng theo hướng, hỗn hợp trực
tuyến, kết hợp tín hiệu và hiệu chỉnh nhiễu; chỉ giữ bản có điểm tốt nhất.

Nếu cải tiến tiếp, tôi sẽ thu thập thêm dữ liệu sạch đại diện cho nhiều môi
trường, giảm báo nhầm theo từng nhóm và bổ sung cơ chế xác nhận thay đổi lineage
hợp lệ. Các lượt chạy lại dùng cùng bộ private; mức tăng chưa chứng minh hiệu
quả trên luồng mới. Mô phỏng cũng không thay thế một tập đánh giá độc lập.
