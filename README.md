# Hướng dẫn sử dụng Character Platform Auto Jump Tool

## Giới thiệu

Đây là công cụ hỗ trợ tự động tính toán và thực hiện cú nhảy trong game
bằng cách nhận diện nhân vật và platform trên màn hình.

------------------------------------------------------------------------

## Các phím chức năng

### E - Chọn platform mục tiêu

-   Khóa platform đang được highlight.
-   Platform này sẽ trở thành mục tiêu cho cú nhảy tiếp theo.
-   Platform được chọn sẽ được tô màu xanh lá đậm.

------------------------------------------------------------------------

### R - Thực hiện một cú nhảy

-   Tính toán khoảng cách từ nhân vật tới platform mục tiêu.
-   Tự động nhấn giữ chuột trong thời gian phù hợp.
-   Tự động nhả chuột để thực hiện cú nhảy.

------------------------------------------------------------------------

### B - Chọn vùng nhận diện

Nhấn **B** hai lần:

1.  Lần đầu chọn góc thứ nhất.
2.  Lần hai chọn góc đối diện.

Sau đó chương trình chỉ nhận diện platform nằm trong vùng này.

------------------------------------------------------------------------

### T - Chế độ Auto thông thường

Sau khi nhấn **T**:

-   Đếm ngược 3 giây.
-   Tự động chọn platform gần con trỏ chuột nhất.
-   Quan sát chuyển động của nhân vật và platform.
-   Tính toán thời điểm nhảy.
-   Tự động nhảy.
-   Chờ nhân vật đáp đất và camera ổn định.
-   Lặp lại liên tục.

Nhấn **T** lần nữa để hủy chế độ Auto.

------------------------------------------------------------------------

### Y - Chế độ Auto chỉ chọn platform đứng yên

Sau khi nhấn **Y**:

-   Đếm ngược 3 giây.
-   Kiểm tra tối đa 3 platform gần con trỏ chuột.
-   Mỗi platform sẽ được theo dõi trong một khoảng thời gian ngắn.
-   Nếu platform di chuyển quá nhiều sẽ bị loại.
-   Chỉ platform gần như đứng yên mới được chọn để nhảy.
-   Nếu không tìm được platform phù hợp thì chế độ Auto sẽ tự dừng.

Nhấn **Y** lần nữa để hủy.

------------------------------------------------------------------------

### U - Dịch điểm nhắm sang trái

Di chuyển điểm nhắm sang bên trái so với tâm platform.

Dùng để hiệu chỉnh khi nhân vật thường nhảy quá xa.

------------------------------------------------------------------------

### I - Dịch điểm nhắm sang phải

Di chuyển điểm nhắm sang bên phải so với tâm platform.

Dùng để hiệu chỉnh khi nhân vật thường nhảy chưa đủ.

------------------------------------------------------------------------

## Quy trình sử dụng

### Nhảy thủ công

1.  Nhấn **B** hai lần để chọn vùng detect.
2.  Đưa chuột tới gần platform cần nhảy.
3.  Nhấn **E** để khóa platform.
4.  Nhấn **R** để thực hiện cú nhảy.

### Nhảy tự động

1.  Đưa chuột tới khu vực có platform mong muốn.
2.  Nhấn **T** hoặc **Y**.
3.  Công cụ sẽ tự động thực hiện các cú nhảy cho đến khi bạn hủy chế độ
    Auto.
