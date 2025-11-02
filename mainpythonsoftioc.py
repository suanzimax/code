# coding=utf-8
import sys
import os
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
import numpy as np
import mvsdk
from PIL import Image, ImageTk
# EPICS soft IOC support
from softioc import softioc, builder
import cothread


class CameraControlApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("工业相机控制")
        self.root.geometry("1280x960")

        # 相机相关变量
        self.hCamera = 0
        self.pFrameBuffer = 0
        self.cap = None
        self.monoCamera = False
        self.is_capturing = False
        self.capture_thread = None

        # GUI变量
        self.frame_rate = tk.DoubleVar(value=30.0)
        self.exposure_time = tk.DoubleVar(value=30.0)
        self.save_path = tk.StringVar(value="./images")

        # 实时反馈变量
        self.actual_frame_rate = tk.DoubleVar(value=0.0)
        self.actual_exposure_time = tk.DoubleVar(value=0.0)
        self.frame_count = 0
        self.last_fps_time = time.time()

        # 图像显示相关
        self.current_frame = None
        self.display_frame = None

        # EPICS PV handles (will be created after camera is opened)
        self.frame_pv = None
        self.width_pv = None
        self.height_pv = None
        self.count_pv = None
        self.timestamp_pv = None

        # 用于 EPICS 发布的帧计数（避免与用于 FPS 的 self.frame_count 混淆）
        self.epics_frame_counter = 0

        self.setup_gui()

    def setup_gui(self):
        """设置GUI界面"""
        # 主框架
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左侧控制面板
        control_frame = ttk.LabelFrame(main_frame, text="相机控制", width=300)
        control_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        control_frame.pack_propagate(False)

        # 相机连接
        ttk.Label(control_frame, text="相机连接:").pack(anchor=tk.W, pady=5)
        self.camera_status_label = ttk.Label(control_frame, text="未连接", foreground="red")
        self.camera_status_label.pack(anchor=tk.W)
        self.camera_name_label = ttk.Label(control_frame, text="相机: 未连接")
        self.camera_name_label.pack(anchor=tk.W, pady=(0, 5))

        ttk.Button(control_frame, text="连接相机", command=self.connect_camera).pack(fill=tk.X, pady=5)
        ttk.Button(control_frame, text="断开相机", command=self.disconnect_camera).pack(fill=tk.X, pady=5)

        # 采集控制
        ttk.Separator(control_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(control_frame, text="采集控制:").pack(anchor=tk.W, pady=5)

        self.capture_button = ttk.Button(control_frame, text="开始采集", command=self.toggle_capture)
        self.capture_button.pack(fill=tk.X, pady=5)

        # 参数设置
        ttk.Separator(control_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(control_frame, text="参数设置:").pack(anchor=tk.W, pady=5)

        # 帧率设置
        frame_rate_frame = ttk.Frame(control_frame)
        frame_rate_frame.pack(fill=tk.X, pady=2)
        ttk.Label(frame_rate_frame, text="帧率:").pack(side=tk.LEFT)
        frame_rate_spin = ttk.Spinbox(frame_rate_frame, from_=1.0, to=100.0, textvariable=self.frame_rate, width=10)
        frame_rate_spin.pack(side=tk.RIGHT)

        # 曝光时间设置
        exposure_frame = ttk.Frame(control_frame)
        exposure_frame.pack(fill=tk.X, pady=2)
        ttk.Label(exposure_frame, text="曝光(ms):").pack(side=tk.LEFT)
        exposure_spin = ttk.Spinbox(exposure_frame, from_=0.1, to=10000.0, textvariable=self.exposure_time, width=10)
        exposure_spin.pack(side=tk.RIGHT)

        # 应用参数按钮
        ttk.Button(control_frame, text="应用参数", command=self.apply_camera_params).pack(fill=tk.X, pady=5)

        # 保存设置
        ttk.Separator(control_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(control_frame, text="保存设置:").pack(anchor=tk.W, pady=5)

        save_path_frame = ttk.Frame(control_frame)
        save_path_frame.pack(fill=tk.X, pady=2)
        ttk.Entry(save_path_frame, textvariable=self.save_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(save_path_frame, text="浏览", command=self.browse_save_path).pack(side=tk.RIGHT, padx=(5, 0))

        ttk.Button(control_frame, text="保存当前图像", command=self.save_current_image).pack(fill=tk.X, pady=5)

        # 实时参数反馈
        ttk.Separator(control_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(control_frame, text="实时参数反馈:").pack(anchor=tk.W, pady=5)

        # 实际帧率显示
        actual_fps_frame = ttk.Frame(control_frame)
        actual_fps_frame.pack(fill=tk.X, pady=2)
        ttk.Label(actual_fps_frame, text="实际帧率:").pack(side=tk.LEFT)
        ttk.Label(actual_fps_frame, textvariable=self.actual_frame_rate).pack(side=tk.RIGHT)

        # 实际曝光时间显示
        actual_exp_frame = ttk.Frame(control_frame)
        actual_exp_frame.pack(fill=tk.X, pady=2)
        ttk.Label(actual_exp_frame, text="实际曝光:").pack(side=tk.LEFT)
        ttk.Label(actual_exp_frame, textvariable=self.actual_exposure_time).pack(side=tk.RIGHT)

        # 状态信息
        ttk.Separator(control_frame, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(control_frame, text="状态信息:").pack(anchor=tk.W, pady=5)

        self.status_text = tk.Text(control_frame, height=8, width=30)
        self.status_text.pack(fill=tk.BOTH, expand=True)

        # 右侧图像显示
        image_frame = ttk.LabelFrame(main_frame, text="图像显示")
        image_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 图像显示标签
        self.image_label = ttk.Label(image_frame)
        self.image_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 图像信息
        info_frame = ttk.Frame(image_frame)
        info_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.image_info_label = ttk.Label(info_frame, text="图像信息: 无")
        self.image_info_label.pack(anchor=tk.W)

    def log_message(self, message):
        """记录日志消息"""
        timestamp = time.strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        self.status_text.insert(tk.END, log_entry)
        self.status_text.see(tk.END)
        print(log_entry.strip())

    def _select_camera_dialog(self, dev_list):
        """
        弹出一个对话框让用户选择相机
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("选择相机")
        # 保持我们之前修改的窗口高度
        dialog.geometry("400x850")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="检测到多台相机，请选择一台：").pack(pady=10, padx=10)

        # 【修改】使用IntVar来存储所选的 *索引*
        selected_index = tk.IntVar()

        # 创建单选按钮
        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20)

        # 【修改】使用 enumerate 来获取索引 i
        for i, dev_info in enumerate(dev_list):
            cam_name = dev_info.GetFriendlyName()
            # 【修改】Radiobutton 的 value 现在是索引 i
            rb = ttk.Radiobutton(list_frame, text=cam_name, value=i, variable=selected_index)
            rb.pack(anchor=tk.W, pady=2)

        # 【修改】默认选中索引 0
        selected_index.set(0)

        # 结果存储
        result = {"index": None}  # 【修改】存储 "index"

        def on_ok():
            # 【修改】获取选中的索引
            result["index"] = selected_index.get()
            dialog.destroy()

        def on_cancel():
            result["index"] = None  # 保持为 None
            dialog.destroy()

            # 按钮框架

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10, fill=tk.X)

        ttk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.LEFT, expand=True, padx=10)
        ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.RIGHT, expand=True, padx=10)

        # 等待对话框关闭
        self.root.wait_window(dialog)

        # 【修改】返回所选的索引
        return result["index"]

    def connect_camera(self):
        """连接相机"""
        try:
            # 枚举相机
            DevList = mvsdk.CameraEnumerateDevice()
            nDev = len(DevList)
            if nDev < 1:
                messagebox.showerror("错误", "未找到相机设备!")
                self.log_message("错误：未找到相机设备")
                return

            DevInfo = None
            if nDev == 1:
                # 只有一台相机，直接选中
                DevInfo = DevList[0]
                self.log_message("检测到 1 台相机，自动连接。")
            else:
                # 有多台相机，弹出选择框
                self.log_message(f"检测到 {nDev} 台相机，请选择...")
                # 【修改】获取返回的 *索引*
                selectedIndex = self._select_camera_dialog(DevList)

                # 【修改】检查用户是否取消
                if selectedIndex is None:
                    self.log_message("用户取消了相机连接。")
                    return

                # 【修改】通过索引从列表中获取 DevInfo 对象
                DevInfo = DevList[selectedIndex]

            # --- 从这里开始，代码和原来一样 ---

            cam_name = DevInfo.GetFriendlyName()
            self.log_message(f"正在连接相机: {cam_name}")

            # 打开相机
            self.hCamera = mvsdk.CameraInit(DevInfo, -1, -1)

            # 获取相机特性描述
            self.cap = mvsdk.CameraGetCapability(self.hCamera)

            # 判断是黑白相机还是彩色相机
            self.monoCamera = (self.cap.sIspCapacity.bMonoSensor != 0)

            # 设置输出格式
            if self.monoCamera:
                mvsdk.CameraSetIspOutFormat(self.hCamera, mvsdk.CAMERA_MEDIA_TYPE_MONO8)
            else:
                mvsdk.CameraSetIspOutFormat(self.hCamera, mvsdk.CAMERA_MEDIA_TYPE_BGR8)

            # 相机模式切换成连续采集
            mvsdk.CameraSetTriggerMode(self.hCamera, 0)

            # 计算RGB buffer所需的大小
            FrameBufferSize = self.cap.sResolutionRange.iWidthMax * self.cap.sResolutionRange.iHeightMax * (
                1 if self.monoCamera else 3)

            # 分配RGB buffer
            self.pFrameBuffer = mvsdk.CameraAlignMalloc(FrameBufferSize, 16)

            # --- 初始化并创建 EPICS PV（尝试兼容多种 builder API） ---
            try:
                # 设备名前缀，可根据需要修改
                builder.SetDeviceName("CAMERA")

                max_w = self.cap.sResolutionRange.iWidthMax
                max_h = self.cap.sResolutionRange.iHeightMax
                channels = 1 if self.monoCamera else 3
                max_len = max_w * max_h * channels

                # 尝试创建 waveform PV（不同版本的 softioc builder 名称可能不同）
                try:
                    self.frame_pv = builder.WaveformOut('Frame', datatype=np.uint8, length=max_len,
                                                         initial_value=np.zeros(max_len, dtype=np.uint8))
                except Exception:
                    try:
                        self.frame_pv = builder.waveformOut('Frame', datatype=np.uint8, length=max_len,
                                                            initial_value=np.zeros(max_len, dtype=np.uint8))
                    except Exception:
                        self.frame_pv = None

                # 数值型元数据（使用 aOut 或 longOut）
                try:
                    self.width_pv = builder.aOut('Width', initial_value=max_w)
                except Exception:
                    try:
                        self.width_pv = builder.longOut('Width', initial_value=max_w)
                    except Exception:
                        self.width_pv = None

                try:
                    self.height_pv = builder.aOut('Height', initial_value=max_h)
                except Exception:
                    try:
                        self.height_pv = builder.longOut('Height', initial_value=max_h)
                    except Exception:
                        self.height_pv = None

                try:
                    self.count_pv = builder.aOut('FrameCounter', initial_value=0)
                except Exception:
                    self.count_pv = None

                # 时间戳（字符串 PV）
                try:
                    self.timestamp_pv = builder.stringOut('Timestamp', initial_value='')
                except Exception:
                    try:
                        self.timestamp_pv = builder.StringOut('Timestamp', initial_value='')
                    except Exception:
                        self.timestamp_pv = None

                # 完成 builder 配置并启动 IOC
                try:
                    builder.LoadDatabase()
                    softioc.iocInit()
                    self.log_message('softIOC 已启动，EPICS PV 已创建（如果支持）')
                except Exception as e:
                    self.log_message(f'softIOC 启动失败: {e}')

                # 启动发布线程（使用 cothread，以免与 tkinter/main thread 并发问题）
                try:
                    cothread.Spawn(self._pv_update_loop)
                except Exception as e:
                    # 如果 Spawn 失败，仍然继续运行应用，但记录日志
                    self.log_message(f'启动 PV 发布线程失败: {e}')
            except Exception as e:
                self.log_message(f'创建 EPICS PV 失败: {e}')


            self.camera_status_label.config(text="已连接", foreground="green")
            self.camera_name_label.config(text=f"相机: {cam_name}")
            self.log_message("相机连接成功")

            # 应用初始参数
            self.apply_camera_params()

        except Exception as e:
            self.log_message(f"相机连接失败: {str(e)}")
            messagebox.showerror("错误", f"相机连接失败: {str(e)}")

    def disconnect_camera(self):
        """断开相机连接"""
        try:
            if self.hCamera != 0:
                if self.is_capturing:
                    self.toggle_capture()

                mvsdk.CameraUnInit(self.hCamera)
                self.hCamera = 0

            if self.pFrameBuffer != 0:
                mvsdk.CameraAlignFree(self.pFrameBuffer)
                self.pFrameBuffer = 0

            self.camera_status_label.config(text="未连接", foreground="red")
            self.camera_name_label.config(text="相机: 未连接")
            self.log_message("相机已断开")

        except Exception as e:
            self.log_message(f"断开相机失败: {str(e)}")

    def apply_camera_params(self):
        """应用相机参数"""
        if self.hCamera == 0:
            return

        try:
            # 设置曝光时间
            exposure_us = self.exposure_time.get() * 1000  # 转换为微秒
            mvsdk.CameraSetAeState(self.hCamera, 0)  # 关闭自动曝光
            mvsdk.CameraSetExposureTime(self.hCamera, exposure_us)

            # 设置帧率（通过曝光时间间接控制）
            # 注意：实际帧率可能受到曝光时间限制

            self.log_message(f"参数已应用 - 曝光时间: {self.exposure_time.get()}ms")

        except Exception as e:
            self.log_message(f"应用参数失败: {str(e)}")

    def toggle_capture(self):
        """切换采集状态"""
        if self.hCamera == 0:
            messagebox.showwarning("警告", "请先连接相机!")
            return

        if not self.is_capturing:
            self.start_capture()
        else:
            self.stop_capture()

    def start_capture(self):
        """开始采集"""
        try:
            mvsdk.CameraPlay(self.hCamera)
            self.is_capturing = True
            self.capture_button.config(text="停止采集")

            # 启动采集线程
            self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
            self.capture_thread.start()

            self.log_message("开始图像采集")

        except Exception as e:
            self.log_message(f"开始采集失败: {str(e)}")

    def stop_capture(self):
        """停止采集"""
        try:
            self.is_capturing = False
            mvsdk.CameraPause(self.hCamera)
            self.capture_button.config(text="开始采集")
            self.log_message("停止图像采集")

        except Exception as e:
            self.log_message(f"停止采集失败: {str(e)}")

    def capture_loop(self):
        """采集循环"""
        while self.is_capturing and self.hCamera != 0:
            try:
                # 从相机取一帧图片
                pRawData, FrameHead = mvsdk.CameraGetImageBuffer(self.hCamera, 200)
                mvsdk.CameraImageProcess(self.hCamera, pRawData, self.pFrameBuffer, FrameHead)
                mvsdk.CameraReleaseImageBuffer(self.hCamera, pRawData)

                # 转换为OpenCV格式
                frame_data = (mvsdk.c_ubyte * FrameHead.uBytes).from_address(self.pFrameBuffer)
                frame = np.frombuffer(frame_data, dtype=np.uint8)
                frame = frame.reshape((FrameHead.iHeight, FrameHead.iWidth,
                                       1 if FrameHead.uiMediaType == mvsdk.CAMERA_MEDIA_TYPE_MONO8 else 3))

                # 更新当前帧
                self.current_frame = frame.copy()
                # 更新 EPICS 帧计数（在采集线程中递增），并每帧同步写入所有 PV
                try:
                    self.epics_frame_counter += 1

                    # 写入 waveform 和元数据（同步每帧更新）
                    try:
                        if self.frame_pv is not None:
                            # 去掉多余的通道维度并展平
                            arr = self.current_frame.squeeze()
                            if arr.dtype != np.uint8:
                                arr = arr.astype(np.uint8)
                            flat = arr.flatten()
                            # 写入波形 PV（注意长度应与创建时一致）
                            self.frame_pv.set(flat)
                    except Exception as e:
                        # 记录但不阻塞采集
                        self.log_message(f"每帧写 Frame PV 失败: {e}")

                    try:
                        if self.width_pv is not None:
                            self.width_pv.set(int(frame.shape[1]))
                    except Exception:
                        pass
                    try:
                        if self.height_pv is not None:
                            self.height_pv.set(int(frame.shape[0]))
                    except Exception:
                        pass
                    try:
                        if self.count_pv is not None:
                            self.count_pv.set(int(self.epics_frame_counter))
                    except Exception:
                        pass
                    try:
                        if self.timestamp_pv is not None:
                            self.timestamp_pv.set(time.strftime("%Y-%m-%d %H:%M:%S"))
                    except Exception:
                        pass
                except Exception:
                    pass

                # DEBUG: 打印当前帧信息（dtype, shape, ndim）
                try:
                    print(self.current_frame.dtype, self.current_frame.shape, self.current_frame.ndim)
                except Exception:
                    pass

                # 更新实时帧率
                self.update_fps()
                # 更新实际曝光时间显示
                self.actual_exposure_time.set(f"{self.exposure_time.get():.2f} ms")

                # 更新GUI显示
                self.root.after(0, self.update_image_display, frame, FrameHead)

            except mvsdk.CameraException as e:
                if e.error_code != mvsdk.CAMERA_STATUS_TIME_OUT:
                    self.log_message(f"采集错误: {e.message}")
            except Exception as e:
                self.log_message(f"采集循环错误: {str(e)}")

    def update_image_display(self, frame, frame_head):
        """更新图像显示"""
        try:
            # 调整图像大小用于显示
            display_frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)

            # 转换为PIL图像
            if len(display_frame.shape) == 3:
                if display_frame.shape[2] == 3:
                    # BGR转RGB
                    display_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                elif display_frame.shape[2] == 1:
                    # 灰度图像
                    display_frame = cv2.cvtColor(display_frame, cv2.COLOR_GRAY2RGB)
            else:
                # 单通道灰度图像
                display_frame = cv2.cvtColor(display_frame, cv2.COLOR_GRAY2RGB)

            pil_image = Image.fromarray(display_frame)
            photo = ImageTk.PhotoImage(pil_image)

            self.image_label.config(image=photo)
            self.image_label.image = photo  # 保持引用

            # 更新图像信息
            info_text = f"尺寸: {frame_head.iWidth}x{frame_head.iHeight}, 格式: {frame_head.uiMediaType}, 时间戳: {frame_head.uiTimeStamp}"
            self.image_info_label.config(text=f"图像信息: {info_text}")

        except Exception as e:
            self.log_message(f"更新显示失败: {str(e)}")

    def _pv_update_loop(self):
        """周期性地将 self.current_frame 发布为 EPICS waveform PV（在 cothread 环境中运行）"""
        # PV 循环现在只做轻量保活或错误记录，无需每帧写入（capture_loop 已负责同步写入）
        while True:
            try:
                # 可在此加入周期性健康检查或其他低频任务
                cothread.Sleep(1.0)
            except Exception as e:
                self.log_message(f"PV 发布循环错误: {e}")
                try:
                    cothread.Sleep(0.5)
                except Exception:
                    time.sleep(0.5)

    def browse_save_path(self):
        """浏览保存路径"""
        path = filedialog.askdirectory(initialdir=self.save_path.get())
        if path:
            self.save_path.set(path)

    def save_current_image(self):
        """保存当前图像"""
        if self.current_frame is None:
            messagebox.showwarning("警告", "没有可保存的图像!")
            return

        try:
            # 创建保存目录
            save_dir = self.save_path.get()
            os.makedirs(save_dir, exist_ok=True)

            # 生成文件名
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"image_{timestamp}.jpg"
            filepath = os.path.join(save_dir, filename)

            # 保存图像
            cv2.imwrite(filepath, self.current_frame)
            self.log_message(f"图像已保存: {filepath}")
            messagebox.showinfo("成功", f"图像已保存到: {filepath}")

        except Exception as e:
            self.log_message(f"保存图像失败: {str(e)}")
            messagebox.showerror("错误", f"保存图像失败: {str(e)}")

    def update_fps(self):
        """更新实时帧率"""
        self.frame_count += 1
        current_time = time.time()
        elapsed = current_time - self.last_fps_time

        if elapsed >= 1.0:  # 每秒更新一次
            fps = self.frame_count / elapsed
            self.actual_frame_rate.set(f"{fps:.2f} FPS")
            self.frame_count = 0
            self.last_fps_time = current_time

    def run(self):
        """运行应用程序"""
        try:
            self.log_message("应用程序启动")
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
            self.root.mainloop()
        except Exception as e:
            self.log_message(f"应用程序运行错误: {str(e)}")
        finally:
            self.cleanup()

    def on_closing(self):
        """关闭应用程序"""
        try:
            if self.is_capturing:
                self.stop_capture()
            self.disconnect_camera()
            self.root.destroy()
        except Exception as e:
            self.log_message(f"关闭应用程序错误: {str(e)}")

    def cleanup(self):
        """清理资源"""
        try:
            if self.hCamera != 0:
                mvsdk.CameraUnInit(self.hCamera)
            if self.pFrameBuffer != 0:
                mvsdk.CameraAlignFree(self.pFrameBuffer)
        except Exception as e:
            print(f"清理资源错误: {str(e)}")


def main():
    """主函数"""
    try:
        app = CameraControlApp()
        app.run()
    except Exception as e:
        print(f"程序启动失败: {str(e)}")
        messagebox.showerror("错误", f"程序启动失败: {str(e)}")


if __name__ == "__main__":
    main()