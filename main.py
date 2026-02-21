from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("helloworld", "YourName", "一个简单的 Hello World 插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # 注册指令的装饰器。指令名为 helloworld。注册成功后，发送 `/helloworld` 就会触发这个指令，并回复 `你好, {user_name}!`
    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        """这是一个 hello world 指令""" # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        user_name = event.get_sender_name()
        message_str = event.message_str # 用户发的纯文本消息字符串
        message_chain = event.get_messages() # 用户所发的消息的消息链 # from astrbot.api.message_components import *
        logger.info(message_chain)
        yield event.plain_result(f"Hello, {user_name}, 你发了 {message_str}!") # 发送一条纯文本消息

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
import asyncio
import os
import zipfile
from pathlib import Path
from typing import Optional, List, Dict

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image, File

# 导入 jmcomic 库（注意：现在 jmcomic 包位于插件子目录中）
import jmcomic
from jmcomic import (
    JmOption, JmHtmlClient, JmApiClient, JmAlbumDetail, JmPhotoDetail,
    JmSearchPage, JmModuleConfig, create_option_by_file, create_option,
    download_album, download_photo, AdvancedDict
)
from jmcomic.jm_plugin import FavoriteFolderExportPlugin

@register("jmcomic_downloader", "你的名字", "禁漫天堂多功能下载插件", "1.1.0")
class JmComicPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        # 基础目录
        self.base_dir = Path(self.config.get("download_dir", "./data/jm_downloads"))
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 默认选项文件路径（用户可通过配置指定）
        self.option_file = self.config.get("option_file", "")
        # 是否打包发送
        self.pack_as_zip = self.config.get("pack_as_zip", True)
        # 预览图片数量
        self.preview_count = self.config.get("preview_count", 3)
        # 登录状态（存放cookies等）
        self.logged_in = False

        # 存储用户独立的配置
        self.user_configs: Dict[str, Dict] = {}

    async def initialize(self):
        """插件加载时初始化"""
        # 预热域名缓存（避免第一次请求超时）
        try:
            await self._run_in_executor(JmModuleConfig.get_html_domain)
        except Exception as e:
            logger.warning(f"预热域名失败：{e}")
        logger.info(f"禁漫插件初始化，下载目录：{self.base_dir}")

    # -------------------- 辅助方法 --------------------
    async def _get_option(self, user_id: str = None) -> JmOption:
        """获取配置对象，支持用户自定义配置"""
        if self.option_file and Path(self.option_file).exists():
            return await self._run_in_executor(create_option_by_file, self.option_file)
        # 创建默认配置，设置基础目录
        return await self._run_in_executor(
            create_option,
            dir_rule='Bd_Aid',  # 按本子ID存放
            download_dir=str(self.base_dir)
        )

    async def _run_in_executor(self, func, *args, **kwargs):
        """在线程池中执行同步阻塞函数"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args, **kwargs)

    # -------------------- 下载功能 --------------------
    @filter.command("jm download")
    async def download_album(self, event: AstrMessageEvent):
        '''下载禁漫本子 用法: /jm download <album_id> [--zip]'''
        args = event.message_str.strip().split()
        if len(args) < 3:
            yield event.plain_result("请提供本子ID，例如：/jm download 123")
            return

        album_id = args[2]
        pack = self.pack_as_zip
        if "--zip" in args:
            pack = True

        yield event.plain_result(f"开始下载本子 {album_id}，请稍候...")
        asyncio.create_task(self._download_and_send(event, album_id, pack))

    @filter.command("jm photo")
    async def download_photo(self, event: AstrMessageEvent):
        '''下载禁漫章节 用法: /jm photo <photo_id> [--zip]'''
        args = event.message_str.strip().split()
        if len(args) < 3:
            yield event.plain_result("请提供章节ID，例如：/jm photo 456")
            return

        photo_id = args[2]
        pack = self.pack_as_zip
        if "--zip" in args:
            pack = True

        yield event.plain_result(f"开始下载章节 {photo_id}，请稍候...")
        asyncio.create_task(self._download_photo_and_send(event, photo_id, pack))

    async def _download_and_send(self, event: AstrMessageEvent, album_id: str, pack: bool):
        try:
            option = await self._get_option(event.get_sender_id())
            await self._run_in_executor(download_album, album_id, option)
            # 根据 option 的 dir_rule 确定下载路径，简化处理：假设下载到 base_dir/album_id
            download_path = self.base_dir / album_id
            await self._handle_download_result(event, album_id, download_path, pack)
        except Exception as e:
            logger.error(f"下载本子 {album_id} 出错: {e}")
            await event.send(event.plain_result(f"下载失败：{str(e)}"))

    async def _download_photo_and_send(self, event: AstrMessageEvent, photo_id: str, pack: bool):
        try:
            option = await self._get_option(event.get_sender_id())
            await self._run_in_executor(download_photo, photo_id, option)
            # 下载路径需根据 option 计算，简化：假设 base_dir/photo_id
            download_path = self.base_dir / photo_id
            await self._handle_download_result(event, photo_id, download_path, pack)
        except Exception as e:
            logger.error(f"下载章节 {photo_id} 出错: {e}")
            await event.send(event.plain_result(f"下载失败：{str(e)}"))

    async def _handle_download_result(self, event: AstrMessageEvent, item_id: str, folder: Path, pack: bool):
        if not folder.exists():
            await event.send(event.plain_result("下载完成但文件夹不存在"))
            return

        if pack:
            zip_path = await self._create_zip(folder)
            if zip_path:
                await event.send(event.chain_result([
                    Plain(f"ID {item_id} 下载完成，打包文件："),
                    File.fromLocalFile(str(zip_path))
                ]))
            else:
                await event.send(event.plain_result("打包失败"))
        else:
            await self._send_preview(event, folder, item_id)

    # -------------------- 搜索功能 --------------------
    @filter.command("jm search")
    async def search_album(self, event: AstrMessageEvent):
        '''搜索禁漫本子 用法: /jm search <关键词> [页码]'''
        args = event.message_str.strip().split(maxsplit=2)
        if len(args) < 2:
            yield event.plain_result("请提供搜索关键词，例如：/jm search 火影")
            return

        keyword = args[1]
        page = 1
        if len(args) >= 3 and args[2].isdigit():
            page = int(args[2])

        yield event.plain_result(f"正在搜索「{keyword}」，第{page}页，请稍候...")
        asyncio.create_task(self._do_search(event, keyword, page))

    async def _do_search(self, event: AstrMessageEvent, keyword: str, page: int):
        try:
            client = JmHtmlClient()
            search_page: JmSearchPage = await self._run_in_executor(client.search, keyword, page)

            if not search_page or len(search_page) == 0:
                await event.send(event.plain_result("没有找到相关本子"))
                return

            lines = [f"搜索「{keyword}」结果（第{page}/{search_page.page_count}页）："]
            for idx, (aid, info) in enumerate(search_page[:10], 1):
                title = info.get('name', '未知标题')
                lines.append(f"{idx}. ID: {aid} | {title}")
            lines.append(f"共{len(search_page)}条，当前第{page}页。使用 /jm detail <id> 查看详情")

            await event.send(event.plain_result("\n".join(lines)))
        except Exception as e:
            logger.error(f"搜索出错: {e}")
            await event.send(event.plain_result(f"搜索失败：{str(e)}"))

    @filter.command("jm detail")
    async def album_detail(self, event: AstrMessageEvent):
        '''查看本子详情 用法: /jm detail <album_id>'''
        args = event.message_str.strip().split()
        if len(args) < 3:
            yield event.plain_result("请提供本子ID，例如：/jm detail 123")
            return

        album_id = args[2]
        asyncio.create_task(self._show_detail(event, album_id))

    async def _show_detail(self, event: AstrMessageEvent, album_id: str):
        try:
            client = JmHtmlClient()
            album: JmAlbumDetail = await self._run_in_executor(client.get_album_detail, album_id)

            if not album:
                await event.send(event.plain_result(f"未找到本子 {album_id}"))
                return

            lines = [
                f"标题：{album.title}",
                f"作者：{album.author}",
                f"收藏数：{album.likes}",
                f"章节数：{len(album)}",
                "章节列表："
            ]
            for idx, photo in enumerate(album[:10], 1):
                lines.append(f"  {idx}. ID: {photo.photo_id} | {photo.name}")
            if len(album) > 10:
                lines.append(f"  ... 还有 {len(album)-10} 个章节")

            await event.send(event.plain_result("\n".join(lines)))
        except Exception as e:
            await event.send(event.plain_result(f"获取详情失败：{str(e)}"))

    # -------------------- 收藏夹功能 --------------------
    @filter.command("jm login")
    async def login(self, event: AstrMessageEvent):
        '''登录禁漫账号 用法: /jm login <cookies> 如 "uin=xxx; skey=yyy"'''
        args = event.message_str.strip().split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("请提供cookies字符串")
            return
        cookies_str = args[1]
        try:
            cookies = dict(item.split("=", 1) for item in cookies_str.split("; ") if "=" in item)
            JmModuleConfig.APP_COOKIES = cookies
            self.logged_in = True
            yield event.plain_result("登录成功")
        except Exception as e:
            yield event.plain_result(f"登录失败：{str(e)}")

    @filter.command("jm favorites")
    async def list_favorites(self, event: AstrMessageEvent):
        '''查看我的收藏夹 需要先登录'''
        if not self.logged_in:
            yield event.plain_result("请先使用 /jm login 登录")
            return
        asyncio.create_task(self._do_list_favorites(event))

    async def _do_list_favorites(self, event: AstrMessageEvent):
        try:
            option = await self._get_option()
            client = option.new_jm_client(impl='api')  # 强制使用 api 客户端
            page = await self._run_in_executor(client.favorite_folder)
            if not page.folder_list:
                await event.send(event.plain_result("收藏夹为空"))
                return

            lines = ["我的收藏夹："]
            for folder in page.folder_list:
                fid = folder['FID']
                fname = folder['name']
                # 获取该收藏夹的第一页
                first_page = await self._run_in_executor(client.favorite_folder_page, fid, 1)
                album_infos = [f"  - {aid} {info.get('name', '')}" for aid, info in first_page[:3]]
                lines.append(f"📁 {fname} (ID: {fid})")
                lines.extend(album_infos if album_infos else ["  (暂无本子)"])
            await event.send(event.plain_result("\n".join(lines)))
        except Exception as e:
            await event.send(event.plain_result(f"获取收藏夹失败：{str(e)}"))

    @filter.command("jm export_favorites")
    async def export_favorites(self, event: AstrMessageEvent):
        '''导出收藏夹为 CSV 文件 需要登录'''
        if not self.logged_in:
            yield event.plain_result("请先使用 /jm login 登录")
            return
        asyncio.create_task(self._do_export_favorites(event))

    async def _do_export_favorites(self, event: AstrMessageEvent):
        try:
            option = await self._get_option()
            # 手动添加插件配置
            export_dir = self.base_dir / 'export'
            export_dir.mkdir(parents=True, exist_ok=True)
            plugin_config = {
                'after_init': [
                    {
                        'plugin': 'favorite_folder_export',
                        'kwargs': {
                            'save_dir': str(export_dir),
                            'zip_enable': False,
                        }
                    }
                ]
            }
            option.plugins = AdvancedDict(plugin_config)
            # 调用插件（同步执行，但可能内部开线程，这里等待一下）
            option.call_all_plugin('after_init')
            # 给插件一点时间完成
            await asyncio.sleep(2)

            csv_files = list(export_dir.glob('*.csv'))
            if csv_files:
                # 发送第一个文件作为示例
                await event.send(event.chain_result([
                    Plain("收藏夹导出成功，示例文件："),
                    File.fromLocalFile(str(csv_files[0]))
                ]))
            else:
                await event.send(event.plain_result("导出完成但未生成文件"))
        except Exception as e:
            await event.send(event.plain_result(f"导出失败：{str(e)}"))

    # -------------------- 配置管理 --------------------
    @filter.command("jm config")
    async def manage_config(self, event: AstrMessageEvent):
        '''配置插件 用法: /jm config set <key> <value> | /jm config show'''
        args = event.message_str.strip().split()
        if len(args) < 3:
            yield event.plain_result("子命令错误：set/show")
            return

        sub = args[2]
        user_id = event.get_sender_id()
        if sub == "set" and len(args) >= 5:
            key = args[3]
            value = args[4]
            if user_id not in self.user_configs:
                self.user_configs[user_id] = {}
            self.user_configs[user_id][key] = value
            yield event.plain_result(f"设置 {key}={value} 成功")
        elif sub == "show":
            config = self.user_configs.get(user_id, {})
            yield event.plain_result(f"当前配置：\n" + "\n".join(f"{k}={v}" for k, v in config.items()))
        else:
            yield event.plain_result("用法：/jm config set <key> <value> 或 /jm config show")

    # -------------------- 工具方法 --------------------
    async def _create_zip(self, folder: Path) -> Optional[Path]:
        """将文件夹打包为 ZIP"""
        zip_path = folder.with_suffix(".zip")
        try:
            await self._run_in_executor(self._zip_folder, folder, zip_path)
            return zip_path if zip_path.exists() else None
        except Exception as e:
            logger.error(f"压缩失败: {e}")
            return None

    def _zip_folder(self, folder: Path, zip_path: Path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, start=folder.parent)
                    zipf.write(file_path, arcname)

    async def _send_preview(self, event: AstrMessageEvent, folder: Path, item_id: str):
        image_files = sorted(
            [f for f in folder.glob("*") if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif')]
        )
        if not image_files:
            await event.send(event.plain_result(f"ID {item_id} 下载完成，但未找到图片"))
            return

        previews = image_files[:self.preview_count]
        msg_chain = [Plain(f"ID {item_id} 下载完成，预览：")]
        for img in previews:
            msg_chain.append(Image.fromLocalFile(str(img)))
        await event.send(event.chain_result(msg_chain))

    # -------------------- 插件终止 --------------------
    async def terminate(self):
        logger.info("禁漫插件卸载")
