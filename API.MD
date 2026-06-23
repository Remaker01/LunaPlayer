# 线上音乐搜索指南

BASE URL: https://music.90svip.cn/

*为保持搜索功能的相对独立性，推荐使用已安装的`httpcore`，`urllib3`进行搜索/下载*

## 数据格式
### 列表
请求方法：POST+XHR

数据类型：application/x-www-form-urlencoded; charset=UTF-8

返回类型：application/json

input: 用户输入

filter：过滤类型，如name, id等。本软件仅实现name

type：提供商，包含netease（网易）、qq、kugou（酷狗）、kuwo四种

page： 页数

### 歌曲/歌词/封面(url/lrc/pic)
请求方法：GET

数据格式：见下面请求示例-请求列表-返回数据部分

所有歌曲以mp3格式返回

歌曲和封面均会重定向

## 请求示例

### 请求列表

**请求代码**

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$session.UserAgent = "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36 Edg/127.0.0.0"
Invoke-WebRequest -UseBasicParsing -Uri "https://music.90svip.cn/" `
-Method "POST" `
-WebSession $session `
-Headers @{
"authority"="music.90svip.cn"
  "method"="POST"
  "path"="/"
  "scheme"="https"
  "accept"="application/json, text/javascript, */*; q=0.01"
  "accept-encoding"="gzip, deflate, br, zstd"
  "accept-language"="zh-CN,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6"
  "dnt"="1"
  "origin"="https://music.90svip.cn" # Important
  "priority"="u=1, i"
  "referer"="https://music.90svip.cn/?name=Test&type=netease" # Important
  "sec-ch-ua"="`"Not)A;Brand`";v=`"99`", `"Microsoft Edge`";v=`"127`", `"Chromium`";v=`"127`""
  "sec-ch-ua-mobile"="?1"
  "sec-ch-ua-platform"="`"Android`""
  "sec-fetch-dest"="empty"
  "sec-fetch-mode"="cors"
  "sec-fetch-site"="same-origin"
  "x-requested-with"="XMLHttpRequest" # Important
} `
-ContentType "application/x-www-form-urlencoded; charset=UTF-8" `
-Body "input=Test&filter=name&type=netease&page=1"
```

**返回数据**

```json5
{
  "data": [
    {
      "link": "https://music.163.com/#/song?id=1822070830", //
      "songid": 1822070830,
      "name": "TEST",
      "artist": "邓典果DDG",
      "lrc": "api.php?get=lrc&type=wy&id=1822070830&sign=4bBWCJjYKnB6M&t=1782018878", //歌词地址
      "url": "api.php?get=url&type=wy&id=1822070830&sign=4bBWCJjYKnB6M&t=1782018878", //下载地址
      "cover": "api.php?get=pic&type=wy&id=1822070830&sign=4bBWCJjYKnB6M&t=1782018878" //封面地址
    },
    {
      "link": "https://music.163.com/#/song?id=3336542475",
      "songid": 3336542475,
      "name": "TEST ME",
      "artist": "ちゃんみな",
      "lrc": "api.php?get=lrc&type=wy&id=3336542475&sign=96xhpnkQk9dgE&t=1782018878",
      "url": "api.php?get=url&type=wy&id=3336542475&sign=96xhpnkQk9dgE&t=1782018878",
      "cover": "api.php?get=pic&type=wy&id=3336542475&sign=96xhpnkQk9dgE&t=1782018878"
    },
    {
      "link": "https://music.163.com/#/song?id=3390037515",
      "songid": 3390037515,
      "name": "Not Exactly (Rinzen Remix)",
      "artist": "deadmau5/Rinzen",
      "lrc": "api.php?get=lrc&type=wy&id=3390037515&sign=a6b5tFLxh6Iek&t=1782018878",
      "url": "api.php?get=url&type=wy&id=3390037515&sign=a6b5tFLxh6Iek&t=1782018878",
      "cover": "api.php?get=pic&type=wy&id=3390037515&sign=a6b5tFLxh6Iek&t=1782018878"
    },
    {
      "link": "https://music.163.com/#/song?id=3387145907",
      "songid": 3387145907,
      "name": "TEST",
      "artist": "YeZippo",
      "lrc": "api.php?get=lrc&type=wy&id=3387145907&sign=8ao1X5OHpjSOU&t=1782018878",
      "url": "api.php?get=url&type=wy&id=3387145907&sign=8ao1X5OHpjSOU&t=1782018878",
      "cover": "api.php?get=pic&type=wy&id=3387145907&sign=8ao1X5OHpjSOU&t=1782018878"
    },
    {
      "link": "https://music.163.com/#/song?id=30482519",
      "songid": 30482519,
      "name": "向天再借五百年",
      "artist": "韩磊",
      "lrc": "api.php?get=lrc&type=wy&id=30482519&sign=11iGRSrggChPg&t=1782018878",
      "url": "api.php?get=url&type=wy&id=30482519&sign=11iGRSrggChPg&t=1782018878",
      "cover": "api.php?get=pic&type=wy&id=30482519&sign=11iGRSrggChPg&t=1782018878"
    }
    // 以下省略
  ],
  "code": 200,
  "error": ""
}
```

### 请求歌词

**请求代码**

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$session.UserAgent = "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36 Edg/127.0.0.0"
Invoke-WebRequest -UseBasicParsing -Uri "https://music.90svip.cn/api.php?get=lrc&type=qq&id=004Y6ZHz2Yaj1X&sign=696fBRAN6JUZA&t=1782019544" `
-WebSession $session `
-Headers @{
"authority"="music.90svip.cn"
  "method"="GET"
  "path"="/api.php?get=lrc&type=qq&id=004Y6ZHz2Yaj1X&sign=696fBRAN6JUZA&t=1782019544"
  "scheme"="https"
  "accept"="*/*"
  "accept-encoding"="gzip, deflate, br, zstd"
  "accept-language"="zh-CN,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6"
  "dnt"="1"
  "priority"="u=1, i"
  "referer"="https://music.90svip.cn/?name=Test&type=qq"
  "sec-ch-ua"="`"Not)A;Brand`";v=`"99`", `"Microsoft Edge`";v=`"127`", `"Chromium`";v=`"127`""
  "sec-ch-ua-mobile"="?1"
  "sec-ch-ua-platform"="`"Android`""
  "sec-fetch-dest"="empty"
  "sec-fetch-mode"="cors"
  "sec-fetch-site"="same-origin"
}
```

**返回数据**

```
[ti:Test]
[ar:Timmies]
[al:Test]
[by:]
[offset:0]
[00:00.00]Test - timmies
[00:01.16]Lyrics by：Timmies
[00:02.32]Composed by：Timmies
[00:03.48]You know my friends tryna tell me that I drink too much
[00:07.15]I know they're right but right now it feels good
[00:11.20]Yeah everyone's tryna tell me I go out too much
[00:14.92]But they don't know that it's all about you
[00:19.19]I feel good I'm all right
[00:23.41]It's 2 AM and I wonder where you are
[00:26.64]If I could turn back time
[00:31.12]We be more than a beautiful scar
[00:34.78]Now I find myself at night out of place
[00:38.99]Looking for your eyes in the crowd
[00:42.41]I'ma break my plans tonight go to space
[00:46.71]Leaving all my troubles behind
[00:49.87]You know my friends tryna tell me that I drink too much
[00:53.55]I know they're right but right now it feels good
[00:57.59]Yeah everyone's tryna tell me I go out too much
[01:01.30]But they don't know that it's all about you
[01:22.89]I saw you on my mind
[01:27.16]You pull me back when I try to move on
[01:30.50]Fallen through a thousand times
[01:34.98]Been drinking about you since you've been gone
[01:38.59]Now I find myself at night out of place
[01:42.78]Looking for your eyes in the crowd
[01:46.05]I'ma break my plans tonight go to space
[01:50.61]Leaving all my troubles behind
[01:53.76]You know my friends tryna tell me that I drink too much
[01:57.43]I know they're right but right now it feels good
# 以下省略
```