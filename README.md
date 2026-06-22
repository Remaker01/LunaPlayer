# SmallPlayer

SmallPlayer 是一个基于 Python 和 PySide6 的本地音乐播放器。

## 功能概览

- 本地音频播放
  - 支持常见音频格式：`mp3`、`flac`、`wav`、`ogg`、`m4a`、`wma`、`aac`、`au`、`opus`、`aiff`
  - 播放 / 暂停 / 停止 / 上一首 / 下一首
  - 进度拖动和音量调节
- 播放列表管理
  - 打开目录并递归扫描音频文件
  - 打开多个本地文件并追加到当前播放列表
  - 支持拖拽文件或文件夹到窗口中导入
  - 支持拖拽调整播放列表顺序
  - 按绝对路径去重，避免同一路径重复加入
- 播放模式
  - 顺序播放
  - 列表循环
  - 单曲循环
- 会话持久化
  - 自动保存当前播放列表到 `m3u8`
  - 自动保存播放模式和上次定位到的歌曲
  - 下次启动时恢复到上次那首歌，但不会自动加载或播放
  - 恢复时会跳过已经不存在的文件
- 在线搜索与下载
  - 支持通过项目当前接入的在线接口搜索歌曲
  - 搜索结果可直接加入当前播放列表
  - 支持下载到本地目录，并自动去重加入当前列表
- 歌词与信息展示
  - 支持桌面歌词窗口
  - 支持读取同名 `.lrc` 歌词文件
  - 支持查看歌曲详情、封面、基础元数据
- 系统集成
  - 托盘图标与托盘菜单
  - 全局空格快捷键播放 / 暂停
  - 设置页与下载目录配置
  - 关于对话框与版本展示

## 运行环境

- Python `3.10+`

仓库里包含 `requirements.txt`，可作为依赖参考。

## 快速开始

### 1. 进入项目目录

```powershell
cd D:\codes\SmallPlayer
```

### 2. 启动应用

```powershell
conda create smallplayer python=3.12
conda activate smallplayer
pip install -r requirements.txt
python main.py
```

也可以直接运行：

```powershell
python app/main.py
```

## 常用操作

### 导入本地音乐

- 菜单“文件 -> 打开目录”会扫描目录并替换当前播放列表
- 菜单“文件 -> 打开文件”会把选中的文件追加到当前播放列表
- 把文件夹拖到窗口中会追加扫描结果
- 把文件拖到窗口中会直接追加

### 在线搜索

- 在右侧搜索面板输入关键字
- 选择搜索结果后可以：
  - 添加到当前播放列表
  - 下载到本地目录

## 测试

可使用项目约定环境运行测试：

```powershell
conda run -n smallplayer python -m unittest
```

也可只跑部分测试，例如：

```powershell
conda run -n smallplayer python -m unittest tests.test_playlist_manager tests.test_main_window
```

## License

本项目当前使用 [MIT License](./LICENSE)。
