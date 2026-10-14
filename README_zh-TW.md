# IQCS16 Spectrum 離線工具包

## 在其他電腦安裝

1. 將 ZIP 完整解壓縮到可以寫入的資料夾，例如 `D:\IQCS16_Tool`。請使用較短的路徑，避免 Windows 的長路徑限制；中文與空白路徑可使用。
2. 雙擊 **`Install.bat`**。安裝時會顯示進度，第一次建置需要數分鐘。
3. 出現 `SUCCESS` / `Installation complete` 後，按任意鍵關閉安裝視窗。
4. 在同一資料夾雙擊 **`IQCS16_spectrum.exe`**，再按「開啟 .cs16」選擇 IQ 檔案。也可把 `.cs16` 檔案拖到 EXE 上開啟。

適用於 **Windows 10／11 x64（Intel／AMD）**。不需預先安裝 Python、radioconda、GNU Radio，不需連上網路或取得管理員權限。安裝檔只使用 ZIP 內附的環境與套件，不向系統安裝 Python，不建立系統 PATH 或登錄設定。

請先解壓縮整包，再執行安裝檔；不要在 ZIP 檢視畫面內直接執行。建議保留至少 1 GB 可用空間。若安裝失敗，查看 `logs\install.log`；修正問題後可重新執行 `Install.bat`。重建前請關閉已開啟的檢視器。

## 檔案位置

解壓縮後已包含：

| 檔案／資料夾 | 用途 |
| --- | --- |
| `Install.bat`、`install.ps1` | 雙擊安裝入口及安裝程序 |
| `packages\python-3.12.10-amd64.zip` | 官方 Python 3.12.10 x64 可攜環境 |
| `packages\wheels` | 所有套件的離線安裝檔 |
| `requirements-offline.txt`、`manifest.json` | 固定套件版本與 SHA-256 檢查碼 |
| `*.py` | 程式原始碼及離線測試 |

安裝後在**同一資料夾內**產生：

| 檔案／資料夾 | 用途 |
| --- | --- |
| `IQCS16_spectrum.exe` | 日後直接雙擊開啟的工具 |
| `app_runtime` | EXE 執行所需的 Python、Qt、NumPy 等環境；必須與 EXE 放在一起 |
| `runtime` | 本資料夾專用的 Python 與建置套件，可供重新建置 |
| `build` | EXE 建置中間檔與 spec |
| `temp`、`cache` | 安裝與建置的暫存檔、快取；都位於工具包資料夾內 |
| `logs` | 安裝紀錄、測試報告、EXE 檢查碼 |

安裝程序會自動檢查檔案完整性、離線安裝必要套件、建置 EXE，再檢查 IQ 位元順序、頻譜、JSON、時間定位、瀑布圖、跳轉與播放。實際錄製檔測試在找不到錄製檔時會略過；合成資料測試仍會執行。

這版採用資料夾形式的 EXE：執行時直接使用旁邊的 `app_runtime`，不需要將整套執行環境解壓到系統暫存目錄。請保留整個資料夾搬移到其他電腦；若只攜帶執行成品，需同時複製 `IQCS16_spectrum.exe` 與 `app_runtime`。

所有工具環境都保留在工具包資料夾內。要移除工具，先關閉檢視器，再刪除此資料夾即可。原始 IQ 檔不會被程式修改。

## IQ 格式與操作

- 支援無檔頭 CS16：Little-endian、signed int16、I0 Q0 I1 Q1，每個複數樣本 4 bytes。
- Fs 優先採用同目錄 `檔名.cs16.json` 的 `sample_rate`；沒有有效 JSON 時預設 100000 samples/s，請確認實際取樣率。
- 修改 Fs、FFT、視窗秒數後按「套用設定」。用上一頁／下一頁、時間跳轉或播放瀏覽資料；播放僅為離線瀏覽。
- 縱軸為相對中心頻率的偏移，色階為相對 dBFS/bin，不是校準功率。
- 請在錄製停止後開檔；本工具不持續追蹤增長的檔案，不控制或發射無線訊號。

## 來源與授權

Python 來自 [Python 官方文件所列的 NuGet 發行管道](https://docs.python.org/3.12/using/windows.html#the-nuget-org-packages)，版本頁為 [python 3.12.10](https://www.nuget.org/packages/python/3.12.10)。套件安裝檔來自 PyPI，版本已固定於需求清單，並保留原始套件內的授權檔。

EXE 使用 [PyInstaller](https://pyinstaller.org/en/stable/usage.html) 建置。此工具包包含檢視器原始碼與套件原始授權資訊；`THIRD_PARTY_NOTICES.txt` 列出版本與授權檔位置，`licenses` 保留套件的授權文字。
