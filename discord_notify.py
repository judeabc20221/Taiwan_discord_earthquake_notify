import sys
import requests
import time
import os
import io
import discord
import logging
from dotenv import load_dotenv
from discord_webhook import DiscordWebhook, DiscordEmbed
from datetime import datetime, timezone, timedelta
from filelock import FileLock

# 總等待秒數會是 wait_time * wait_seconds
# 有感地震發生後 等候地震報告的次數
wait_time = 30
# 每次未等到地震報告後 下一次抓取的間隔
wait_seconds = 30

# 定義鎖
loop_lock = FileLock( "loop.lock" )
file_lock = FileLock( "file.lock" )

# 拿取現在最新的地震報告id
def get_last_num():
    try:
        with open( "last_id.txt", 'r' ) as file:
            id = int( file.read() )
            return id
    except Exception as e:
        return None

# 將地震報告id更新
def write_last_num( id: int ):
    try:
        with file_lock:
            with open( "last_id.txt", 'w' ) as file:
                file.write( str( id ) )
    except Exception as e:
        return None
    
# 解析氣象署回傳的發生時間
# 2026/09 起 API 改為 ISO 8601 格式 (例如 2026-09-22T05:16:13+08:00)
# 舊格式為 2026-09-22 05:16:13，這裡兩種都支援
def parse_origin_time( value: str ) -> datetime:
    try:
        return datetime.fromisoformat( value )
    except ValueError:
        return datetime.strptime( value, "%Y-%m-%d %H:%M:%S" )

# 解析地牛Wake Up v4.2.0+ 傳來的連動參數 (--key=value 格式)
def parse_eew_args( argv ):
    params = {}
    for arg in argv:
        if arg.startswith( "--" ) and "=" in arg:
            key, _, value = arg[2:].partition( "=" )
            params[ key ] = value
    return params

# 地牛傳來的震度格式為 5+ / 5-，轉成 5強 / 5弱
def format_intensity( value: str ) -> str:
    return value.replace( "+", "強" ).replace( "-", "弱" )

# 在 Embed 裡插入一個看不見的空白列，讓不同類別的欄位之間有間距
def add_spacer( embed: DiscordEmbed ):
    embed.add_embed_field( name = "\u200b", value = "\u200b", inline = False )

# 建立地震預警訊息
# 回傳 (content, embed)
# content 是純文字摘要，手機通知會直接顯示
# embed 只放震央開始的細節欄位，不放標題和描述
def build_eew_message( city: str, area: str, eew: dict ):
    content = f"🚨 {city}即將發生地震！"
    details = []
    if "magnitude" in eew:
        details.append( f"預估規模 {eew['magnitude']}" )
    if "local-intensity" in eew:
        details.append( f"預估震度 {format_intensity( eew['local-intensity'] )} 級" )
    if "remaining-time" in eew:
        details.append( f"震波約 {eew['remaining-time']} 秒後抵達" )
    if details:
        content += "\n" + "，".join( details )

    embed = DiscordEmbed( color = 15548997 )
    if "epicenter-lat" in eew and "epicenter-lon" in eew:
        embed.add_embed_field( name = "📍 震央", value = f"北緯 {eew['epicenter-lat']}°　東經 {eew['epicenter-lon']}°", inline = False )
        add_spacer( embed )
    # 深度 / 最大震度 / 預估抵達 放在同一行 (分開的 inline 欄位在手機上會被疊起來)
    items = []
    if "depth" in eew:
        items.append( f"深度 **{eew['depth']} 公里**" )
    if "max-intensity" in eew:
        items.append( f"最大震度 **{format_intensity( eew['max-intensity'] )} 級**" )
    lines = []
    if items:
        lines.append( "　".join( items ) )
    if "arrival-time" in eew:
        try:
            arrival = datetime.fromtimestamp( int( eew["arrival-time"] ), tz = timezone( timedelta( hours = 8 ) ) )
            lines.append( f"預估抵達 **{arrival.strftime( '%H:%M:%S' )}**" )
        except ( ValueError, OSError ):
            pass
    if lines:
        embed.add_embed_field( name = "📈 地震資訊", value = "\n".join( lines ), inline = False )
    embed.set_footer( text = "地震速報", icon_url = "https://i.imgur.com/6I4Z7Rq.png" )
    embed.set_timestamp()
    return content, embed

# 氣象署報告顏色對應 Embed 側邊色條
REPORT_COLORS = {
    "綠色": 5763719,
    "黃色": 16705372,
    "橙色": 15105570,
    "紅色": 15548997,
}

# 建立地震報告訊息
# 回傳 (content, embed)
# content 是氣象署的報告文字，手機通知會顯示這行加上報告圖縮圖
# embed 放整理過的細節欄位，圖片由主程式另外用附件方式加上
def build_report_message( city: str, now_data: dict ):
    content = f"📢 {now_data['ReportContent']}"
    info = now_data["EarthquakeInfo"]
    origin_time = parse_origin_time( info["OriginTime"] )

    embed = DiscordEmbed( color = REPORT_COLORS.get( now_data.get( "ReportColor" ), 1940253 ) )
    embed.add_embed_field( name = "🕐 發生時間", value = origin_time.strftime( "%Y年%m月%d日 %H:%M:%S" ), inline = False )
    embed.add_embed_field( name = "📍 震央", value = info["Epicenter"]["Location"].replace( "  ", " " ), inline = False )
    add_spacer( embed )
    items = [
        f"規模 **{info['EarthquakeMagnitude']['MagnitudeValue']}**",
        f"深度 **{info['FocalDepth']} 公里**",
    ]

    # 各地震度
    # ShakingArea 裡 InfoStatus 為 observe 的是各縣市實際觀測值 (已按震度大小排序)
    # 沒有 InfoStatus 的是氣象署另外附的「最大震度X級地區」分組摘要，會跟前面重複，略過
    # 氣象署用「臺」、.env 可能用「台」，比對時統一
    areas = now_data.get( "Intensity", {} ).get( "ShakingArea", [] )
    areas = [ a for a in areas if a.get( "InfoStatus" ) == "observe" ] or areas
    # 規模 / 深度 放在同一行 (分開的 inline 欄位在手機上會被疊起來)，最大震度另起一行
    value = "　".join( items )
    if areas:
        value += f"\n最大震度 **{areas[0]['AreaIntensity']}**（{areas[0]['CountyName']}）"
    embed.add_embed_field( name = "📈 地震資訊", value = value, inline = False )
    if areas:
        my_city = city.replace( "臺", "台" )
        groups = {}
        for a in areas:
            name = a["CountyName"]
            if name.replace( "臺", "台" ) == my_city:
                name = f"**{name}**"
            groups.setdefault( a["AreaIntensity"], [] ).append( name )
        lines = [ f"{level}　{'、'.join( names )}" for level, names in groups.items() ]
        add_spacer( embed )
        embed.add_embed_field( name = "📊 各地震度", value = "\n".join( lines ), inline = False )

    embed.set_footer( text = "地震速報", icon_url = "https://i.imgur.com/6I4Z7Rq.png" )
    embed.set_timestamp( origin_time.timestamp() )
    return content, embed

# 初始化日誌設定
def setup_logger():
    log_filename = "earthquake_log.txt"
    logging.basicConfig(
        filename=log_filename,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
# 使用 logging 替代 print
def log_message(message, level="info"):
    if level == "info":
        logging.info(message)
    elif level == "warning":
        logging.warning(message)
    elif level == "error":
        logging.error(message)
    elif level == "critical":
        logging.critical(message)


if __name__ == "__main__":
    try:
        load_dotenv()
        setup_logger()
        log_message( "開始執行地震報告程式" )
        # discord webhook
        discord_webhook_url = [ item.strip() for item in os.getenv( "discord_webhook_url" ).split( "," ) ]
        # 氣象資料開放平台api
        earthquake_data_url = os.getenv( "earthquake_api" )
        earthquake_data_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?Authorization=" + earthquake_data_url + "&limit=10&format=JSON"
        city = os.getenv( "city" )
        area = os.getenv( "area" )
        # 地牛Wake Up v4.2.0 起以 --key=value 格式傳遞連動參數 (需申請會員)
        # 例如 --magnitude=7.3 --local-intensity=5+ --remaining-time=20
        eew = parse_eew_args( sys.argv[1:] )
        log_message( f"收到連動參數：{sys.argv[1:]}" )
        # 預警訊息：純文字摘要 (通知顯示用) + 細節 Embed
        msg, eew_embed = build_eew_message( city, area, eew )
        for url in discord_webhook_url:
            webhook = DiscordWebhook( url = url, content = msg )
            webhook.add_embed( eew_embed )
            execute = webhook.execute()
            if execute.status_code != 200:
                print( "discord webhook網址錯誤，請按照README.md說明將網址放進.env當中" )
                log_message( "discord webhook網址錯誤，請按照README.md說明將網址放進.env當中", level = "error" )
                time.sleep( 10 )
                exit()
        log_message( msg )

        # 更新檔案中的上個地震id
        response = requests.get( earthquake_data_url )
        if response.status_code == 200 :
            data = response.json()
            if data is not None and data["success"] == "true":
                now_data = data["records"]["Earthquake"][0]
                now_id = now_data["EarthquakeNo"]
                write_last_num( now_id )
        else:
            print( "中央氣象署金鑰錯誤，請按照README.md的說明將金鑰放進.env當中" )
            log_message( "中央氣象署金鑰錯誤，請按照README.md的說明將金鑰放進.env當中", level = "error" )
            time.sleep( 10 )
            exit()

        for i in range( wait_time ):
            response = requests.get( earthquake_data_url )

            if response.status_code == 200 :
                data = response.json()

                if data is not None and data["success"] == "true":
                    try:
                        with loop_lock:
                            # 在新報告產生前 拿取上次地震的id
                            last_id = get_last_num()
                            # 拿取目前最新的地震資訊
                            now_data = data["records"]["Earthquake"][0]
                            now_id = now_data["EarthquakeNo"]

                            # 代表地震報告還未產生 先等候一段時間再抓取
                            if now_id == last_id:
                                print( f"地震報告尚未發布，{wait_seconds}秒後會再自動抓取一次\n" )
                                log_message( f"地震報告尚未發布，{wait_seconds}秒後會再自動抓取一次" )
                            # 新的報告已產生
                            else:
                                print( "檢測到新地震報告 開始收集相關資訊" )
                                log_message( "檢測到新地震報告 開始收集相關資訊" )
                                report_content, embed = build_report_message( city, now_data )
                                print( "開始下載地震報告圖檔" )
                                log_message( "開始下載地震報告圖檔" )
                                try:
                                    image = requests.get( now_data["ReportImageURI"] )
                                    if image.status_code is None or image.status_code != 200 :
                                        log_message( "圖片獲取失敗 不傳送圖檔", level = "warning" )
                                        print( "圖片獲取失敗 不傳送圖檔" )
                                    else:
                                        try:
                                            with open( f"lastest_report.png", "wb" ) as image_file:
                                                image_file.write( image.content )
                                            embed.set_image( url = "attachment://lastest_report.png" )
                                            print( "圖片下載成功" )
                                            log_message( "圖片下載成功", )
                                        except Exception as e:
                                            print( "圖片下載失敗 不傳送圖檔" )
                                            print( "錯誤訊息：" )
                                            print( e )
                                            log_message( f"圖片下載失敗 不傳送圖檔 錯誤訊息:{e}", level = "warning" )
                                except requests.exceptions.Timeout as e :
                                    log_message( f"圖片獲取超時 錯誤訊息：{e}", level = "warning" )
                                    print( f"圖片獲取超時 錯誤訊息：{e}" )

                                # 傳給不同的伺服器
                                for url in discord_webhook_url:
                                    # content 會顯示在手機通知上 (有圖片附件時通知不會顯示 Embed 的文字)
                                    webhook = DiscordWebhook( url = url, content = report_content )
                                    webhook.add_embed( embed )
                                    webhook.add_file( image.content, filename = "lastest_report.png" )
                                    execute = webhook.execute()
                                print( "訊息傳送成功" )
                                log_message( "訊息傳送成功" )
                                write_last_num( now_id )
                                sys.exit()
                    except Exception as e:
                        print( f"迴圈有異常，{e}\n" )
                        log_message( f"迴圈有異常，{e}", level = "error" )
                        sys.exit()

                    time.sleep( wait_seconds )
                        
        print( "地震資料未抓取，不傳送地震報告" )
        log_message( "地震資料未抓取，不傳送地震報告", level = "error" )
    except Exception as e:
        log_message( f"發生錯誤，{e}", level = "error" )