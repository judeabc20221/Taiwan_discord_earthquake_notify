import sys
import requests
import time
import os
import io
import discord
import logging
from dotenv import load_dotenv
from discord_webhook import DiscordWebhook, DiscordEmbed
from datetime import datetime
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
        #magnitude = str( sys.argv[1] ).replace( "+", "強" ).replace( "-", "弱" )
        #second = str( sys.argv[2] )
        msg = f"{city}即將發生地震"
        for url in discord_webhook_url:
            webhook = DiscordWebhook( url = url, content = f"{city}即將發生地震" )
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
                                embed = DiscordEmbed(
                                    title = "地震報告",
                                    description = now_data["ReportContent"],
                                    color = 1940253
                                )
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
                                    embed.set_footer( text = "地震速報", icon_url = "https://i.imgur.com/6I4Z7Rq.png" )
                                except requests.exceptions.Timeout as e :
                                    log_message( f"圖片獲取超時 錯誤訊息：{e}", level = "warning" )
                                    print( f"圖片獲取超時 錯誤訊息：{e}" )

                                # 新增地震的時間規模 深度 震央
                                info = now_data["EarthquakeInfo"]
                                origin_time = datetime.strptime( info["OriginTime"], "%Y-%m-%d %H:%M:%S" )
                                # 提取每個數字
                                year = origin_time.year
                                month = origin_time.month
                                day = origin_time.day
                                hour = origin_time.hour
                                minute = origin_time.minute
                                second = origin_time.second
                                embed.add_embed_field( name = "發生時間", value = f"{year}年{month}月{day}日 {hour}點{minute}分{second}秒", inline = False )
                                embed.add_embed_field( name = "規模", value = f"`{ info['EarthquakeMagnitude']['MagnitudeValue'] }`", inline = True )
                                embed.add_embed_field( name = "深度", value = f"`{ info['FocalDepth'] }`公里", inline = True )
                                embed.add_embed_field( name = "震央", value = f"{ info['Epicenter']['Location'].replace( '  ', '``' ) } ", inline = False )
                                # 傳給不同的伺服器
                                for url in discord_webhook_url:
                                    webhook = DiscordWebhook( url = url )
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
