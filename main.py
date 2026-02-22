import asyncio
import functools
import os
import zipfile
import traceback
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, File, Node, Image

# 导入本地 jmcomic 库
from jmcomic import (
    JmOption,
    JmAlbumDetail,
    JmPhotoDetail,
    JmSearchPage,
    JmModuleConfig,
    JmcomicException,
    MissingAlbumPhotoException,
    RequestRetryAllFailException,
    create_option_by_file,
    download_album,
    download_photo,
    DirRule,
    ExceptionTool,
    time_stamp,
    current_thread,
    fix_windir_name,
    write_text,
    mkdir_if_not_exists,
    workspace
)
from jmcomic import JmMagicConstants
from jmcomic.jm_plugin import Img2pdfPlugin
from jmcomic.jm_downloader import JmDownloader  # 用于自定义范围下载器

# -------------------- 依赖自动安装 --------------------
def ensure_dependencies():
    """检查并自动安装必需的依赖"""
    required = {
        'jmcomic': 'jmcomic',
        'PIL': 'Pillow',
        'img2pdf': 'img2pdf'
    }
    missing = []
    for module, pkg in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pkg)
    if missing:
        logger.warning(f"检测到缺少依赖: {', '.join(missing)}，正在尝试自动安装...")
        for pkg in missing:
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
                logger.info(f"成功安装 {pkg}")
            except Exception as e:
                logger.error(f"自动安装 {pkg} 失败: {e}")
                logger.info(f"请手动执行: pip install {pkg}")
                raise ImportError(f"缺少必要依赖 {pkg}，请手动安装后重试。")
    logger.info("所有依赖检查通过")

ensure_dependencies()

# -------------------- 插件配置默认值 --------------------
DEFAULT_OPTION_FILE = Path(__file__).parent / "assets" / "option" / "option_workflow_download.yml"


@register("jmcomic_downloader", "你的名字", "禁漫下载 PDF/ZIP 插件（支持范围下载与两种清理模式）", "2.6.2")
class JmComicPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 确保插件所需目录存在
        self._ensure_dirs()

        # 全局下载根目录
        self.global_base_dir = Path(self.config.get("download_dir", "./data/jm_downloads"))
        self.global_base_dir.mkdir(parents=True, exist_ok=True)

        # 默认选项文件路径
        self.option_file = self.config.get("option_file", str(DEFAULT_OPTION_FILE))
        if not Path(self.option_file).exists():
            self.option_file = None
            logger.warning("未找到默认 option 配置文件，将使用 jmcomic 内置默认配置。")

        # 清理模式
        self.cleanup_mode = self.config.get("cleanup_mode", "count")
        self.max_albums = self.config.get("max_albums", 10) if self.cleanup_mode == "count" else 0

        # 是否删除临时封面文件
        self.delete_temp_cover = self.config.get("delete_temp_cover", True)

        # 是否启用 jmcomic 内部日志
        if not self.config.get("enable_jm_log", False):
            JmModuleConfig.disable_jm_log()

        # PDF 依赖已由 ensure_dependencies 保证
        self.has_img2pdf = True

        # 注册异常监听器
        self._register_exception_listener()

        # 预热域名
        asyncio.create_task(self._warmup())

    def _ensure_dirs(self):
        """创建插件运行所需的目录结构"""
        default_option_dir = Path(__file__).parent / "assets" / "option"
        default_option_dir.mkdir(parents=True, exist_ok=True)

    async def _warmup(self):
        """预热域名，避免第一次请求超时"""
        try:
            await asyncio.to_thread(JmModuleConfig.get_html_domain)
        except Exception as e:
            logger.warning(f"预热域名失败: {e}")

    def _register_exception_listener(self):
        """注册异常监听器，将 jmcomic 异常保存到日志文件"""
        log_dir = self.global_base_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        def exception_listener(e: JmcomicException):
            try:
                resp = e.context.get(ExceptionTool.CONTEXT_KEY_RESP, None)
                suffix = resp.url if resp else str(time_stamp())
                name = '-'.join([
                    e.__class__.__name__,
                    current_thread().name,
                    fix_windir_name(suffix)
                ])
                log_path = log_dir / f"【错误】{name}.log"

                content = [f"异常类型: {type(e).__name__}", f"消息: {e.msg}"]
                for k, v in e.context.items():
                    content.append(f"{k}: {v}")
                if resp and hasattr(resp, 'text'):
                    content.append(f"响应文本: {resp.text[:1000]}")

                write_text(str(log_path), '\n'.join(content))
                logger.info(f"异常已记录至 {log_path}")
            except Exception as ex:
                logger.error(f"异常监听器自身出错: {ex}")

        JmModuleConfig.register_exception_listener(JmcomicException, exception_listener)

    # -------------------- 辅助方法 --------------------
    async def _get_option(self, user_id: str = None, cmd_overrides: dict = None) -> JmOption:
        if self.option_file and Path(self.option_file).exists():
            option = await asyncio.to_thread(create_option_by_file, self.option_file)
        else:
            option = await asyncio.to_thread(JmOption.default)

        if user_id:
            user_dir = self.global_base_dir / user_id
        else:
            user_dir = self.global_base_dir
        option.dir_rule.base_dir = str(user_dir)

        if cmd_overrides:
            self._apply_overrides(option, cmd_overrides)

        return option

    def _apply_overrides(self, option: JmOption, overrides: dict):
        dir_rule = overrides.get('dir_rule')
        if dir_rule:
            option.dir_rule = DirRule(dir_rule, base_dir=option.dir_rule.base_dir)

        impl = overrides.get('client_impl')
        if impl:
            option.client.impl = impl

        suffix = overrides.get('suffix')
        if suffix:
            option.download.image.suffix = suffix if suffix.startswith('.') else f'.{suffix}'

    async def _run_sync(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

    async def _safe_call(self, func, *args, **kwargs):
        try:
            return await self._run_sync(func, *args, **kwargs)
        except MissingAlbumPhotoException as e:
            raise Exception(f"本子/章节不存在: {e}")
        except RequestRetryAllFailException as e:
            raise Exception(f"请求重试失败，请稍后重试: {e}")
        except JmcomicException as e:
            raise Exception(f"jmcomic 错误: {e}")
        except Exception as e:
            logger.error(traceback.format_exc())
            raise Exception(f"未知错误: {e}")

    async def _safe_call_with_timeout(self, func, timeout=30, *args, **kwargs):
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, functools.partial(func, *args, **kwargs)),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            raise
        except MissingAlbumPhotoException as e:
            raise Exception(f"本子/章节不存在: {e}")
        except RequestRetryAllFailException as e:
            raise Exception(f"请求重试失败，请稍后重试: {e}")
        except JmcomicException as e:
            raise Exception(f"jmcomic 错误: {e}")
        except Exception as e:
            logger.error(traceback.format_exc())
            raise Exception(f"未知错误: {e}")

    # -------------------- 创建范围下载器 --------------------
    def _create_range_downloader(self, start: int, end: int):
        """返回一个自定义 JmDownloader 类，仅下载指定范围章节"""
        class RangeDownloader(JmDownloader):
            def do_filter(self, detail):
                if detail.is_album():
                    album_len = len(detail)
                    s = max(0, start - 1)
                    e = min(album_len, end)
                    if s >= e:
                        return []
                    return detail[s:e]
                return detail
        return RangeDownloader

    # -------------------- 命令解析（支持范围） --------------------
    def _parse_album_command(self, args: List[str]) -> Tuple[str, Optional[Tuple[int, int]]]:
        """解析 /jm download 和 /jmz 命令，返回 (album_id, (start,end) or None)"""
        album_id = args[2]  # 因为命令是 "/jm download 123"，索引2是 ID
        start = end = None
        if len(args) >= 4:
            range_str = args[3]
            if '-' in range_str:
                parts = range_str.split('-')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    start = int(parts[0])
                    end = int(parts[1])
            elif range_str.isdigit():
                start = end = int(range_str)
        return album_id, (start, end) if start is not None else None

    # -------------------- 命令 --------------------
    @filter.command("jm download")
    async def command_jm_download(self, event: AstrMessageEvent):
        """下载本子并转为 PDF：/jm download <本子号> [范围]  范围示例：1-10 或 5"""
        args = event.message_str.strip().split()
        if len(args) < 3:
            yield event.plain_result("请提供本子ID，例如：/jm download 123")
            return
        album_id, range_tuple = self._parse_album_command(args)
        overrides = {}
        if range_tuple:
            overrides['chapter_range'] = range_tuple
            start, end = range_tuple
            yield event.plain_result(f"开始下载本子 {album_id} 第{start}~{end}章，请稍候...")
        else:
            yield event.plain_result(f"开始下载本子 {album_id}，请稍候...")
        asyncio.create_task(self._download_album_task(event, album_id, pack=False, overrides=overrides))

    @filter.command("jmz")
    async def command_jmz(self, event: AstrMessageEvent):
        """下载本子并打包 ZIP：/jmz <本子号> [范围]"""
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供本子ID，例如：/jmz 123")
            return
        album_id = args[1]
        start = end = None
        range_tuple = None
        if len(args) >= 3:
            range_str = args[2]
            if '-' in range_str:
                parts = range_str.split('-')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    start = int(parts[0])
                    end = int(parts[1])
            elif range_str.isdigit():
                start = end = int(range_str)
            if start is not None:
                range_tuple = (start, end)
        overrides = {}
        if range_tuple:
            overrides['chapter_range'] = range_tuple
            yield event.plain_result(f"开始下载本子 {album_id} 第{start}~{end}章（ZIP打包），请稍候...")
        else:
            yield event.plain_result(f"开始下载本子 {album_id}（ZIP打包），请稍候...")
        asyncio.create_task(self._download_album_task(event, album_id, pack=True, overrides=overrides))

    @filter.command("jms")
    async def command_jms(self, event: AstrMessageEvent):
        """搜索本子：/jms <关键词> [页码]"""
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供搜索关键词，例如：/jms 火影")
            return
        keyword = args[1]
        page = 1
        if len(args) >= 3 and args[2].isdigit():
            page = int(args[2])
        yield event.plain_result(f"正在搜索「{keyword}」第{page}页，请稍候...")
        asyncio.create_task(self._do_search(event, keyword, page))

    @filter.command("jmr")
    async def command_jmr(self, event: AstrMessageEvent):
        """获取排行榜：/jmr [week|day] [页码] （默认月榜）"""
        args = event.message_str.strip().split()
        rank_type = "month"
        page = 1
        if len(args) >= 2:
            if args[1].lower() in ("week", "day"):
                rank_type = args[1].lower()
            if len(args) >= 3 and args[2].isdigit():
                page = int(args[2])
        yield event.plain_result(f"正在获取{rank_type}榜第{page}页，请稍候...")
        asyncio.create_task(self._do_ranking(event, rank_type, page))

    @filter.command("jm detail")
    async def command_detail(self, event: AstrMessageEvent):
        """查看本子详情：/jm detail <本子号>"""
        args = event.message_str.strip().split()
        if len(args) < 3:
            yield event.plain_result("请提供本子ID，例如：/jm detail 123")
            return
        album_id = args[2]
        # 重要：_do_detail 是异步生成器，必须用 async for 迭代，不能使用 create_task
        async for ret in self._do_detail(event, album_id):
            yield ret

    @filter.command("jm help")
    async def command_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
【禁漫下载插件使用说明】

/jm download <本子号> [范围]   下载本子指定范围章节（默认全部），生成PDF发送。范围示例：1-10 或 5
/jmz <本子号> [范围]         下载本子并打包ZIP发送
/jms <关键词> [页码]         搜索本子，默认第1页
/jmr [week|day] [页码]       排行榜，默认月榜第1页
/jm detail <本子号>          查看本子详情（含封面和标签）
/jm help                     显示本帮助信息

清理模式（可在插件配置中修改）：
- count：保留最多 max_albums 个本子，超过自动删除最旧
- after_send：每次发送后立即删除本次下载的所有文件（包括原图和生成的文件）

示例：
/jm download 123             下载本子123全部章节为PDF
/jm download 123 1-5         下载第1-5章为PDF
/jmz 123                      下载本子123全部章节为ZIP
/jms 火影 2                   搜索火影第2页
/jmr                          月榜第1页
/jmr week 2                   周榜第2页
/jm detail 123                查看本子123详情
        """.strip()
        yield event.plain_result(help_text)

    # -------------------- 下载任务 --------------------
    async def _download_album_task(self, event: AstrMessageEvent, album_id: str, pack: bool, overrides: dict):
        user_id = event.get_sender_id()
        try:
            option = await self._get_option(user_id, overrides)
            chapter_range = overrides.get('chapter_range')
            downloader_class = None
            if chapter_range:
                start, end = chapter_range
                downloader_class = self._create_range_downloader(start, end)

            result = await self._safe_call(download_album, album_id, option, downloader=downloader_class)
            if isinstance(result, tuple) and len(result) == 2:
                album, downloader = result
            else:
                album = result
                downloader = None

            album_dir = Path(option.dir_rule.decide_album_root_dir(album))

            if pack:
                zip_path = await self._handle_download_result(event, album_id, album_dir, pack=True)
                sent_files = [zip_path] if zip_path else []
            else:
                plugin = Img2pdfPlugin(option)
                pdf_dir = self.global_base_dir / user_id / "pdfs"
                pdf_dir.mkdir(parents=True, exist_ok=True)

                await self._run_sync(
                    plugin.invoke,
                    album=album,
                    downloader=downloader,
                    pdf_dir=str(pdf_dir),
                    filename_rule='Aid',
                    delete_original_file=False,
                )

                await asyncio.sleep(1)
                pdf_path = pdf_dir / f"{album_id}.pdf"
                if pdf_path.exists():
                    await event.send(event.chain_result([
                        Plain(f"本子 {album_id} 下载完成，已转换为 PDF："),
                        File(file=str(pdf_path), name=pdf_path.name)
                    ]))
                    sent_files = [pdf_path]
                else:
                    pdf_files = list(pdf_dir.glob(f"{album_id}*.pdf"))
                    if pdf_files:
                        pdf_path = pdf_files[0]
                        await event.send(event.chain_result([
                            Plain(f"本子 {album_id} 下载完成，已转换为 PDF："),
                            File(file=str(pdf_path), name=pdf_path.name)
                        ]))
                        sent_files = [pdf_path]
                    else:
                        await event.send(event.plain_result("PDF 生成失败，未找到生成的 PDF 文件"))
                        sent_files = []

            if sent_files and self.cleanup_mode == "after_send":
                asyncio.create_task(self._delete_after_send(album_dir, sent_files))
            elif self.cleanup_mode == "count":
                asyncio.create_task(self._cleanup_old_albums(user_id))

        except Exception as e:
            await event.send(event.plain_result(f"下载失败: {e}"))

    async def _handle_download_result(self, event: AstrMessageEvent, item_id: str, folder: Path, pack: bool) -> Optional[Path]:
        if not folder.exists() or not any(folder.iterdir()):
            await event.send(event.plain_result("下载完成但文件夹为空，可能未成功下载任何图片。"))
            return None

        if pack:
            zip_path = await self._create_zip(folder)
            if zip_path and zip_path.exists():
                await event.send(event.chain_result([
                    Plain(f"ID {item_id} 下载完成，打包文件："),
                    File(file=str(zip_path), name=zip_path.name)
                ]))
                return zip_path
            else:
                await event.send(event.plain_result("打包失败"))
                return None
        return None

    async def _delete_after_send(self, album_dir: Path, sent_files: List[Path]):
        """after_send 模式：删除原图片文件夹和已发送的文件"""
        try:
            if album_dir.exists():
                shutil.rmtree(album_dir, ignore_errors=True)
                logger.info(f"已删除原图片文件夹: {album_dir}")
            for f in sent_files:
                if f.exists():
                    f.unlink()
                    logger.info(f"已删除已发送文件: {f}")
        except Exception as e:
            logger.error(f"after_send 删除文件失败: {e}")

    async def _create_zip(self, folder: Path) -> Optional[Path]:
        zip_path = folder.with_suffix(".zip")
        try:
            await self._run_sync(self._zip_folder, folder, zip_path)
            return zip_path if zip_path.exists() else None
        except Exception as e:
            logger.error(f"压缩失败: {e}")
            return None

    @staticmethod
    def _zip_folder(folder: Path, zip_path: Path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, start=folder.parent)
                    zipf.write(file_path, arcname)

    # -------------------- 清理旧本子（count模式） --------------------
    async def _cleanup_old_albums(self, user_id: str):
        if self.cleanup_mode != "count" or self.max_albums <= 0:
            return

        user_root = self.global_base_dir / user_id
        if not user_root.exists():
            return

        exclude_dirs = {"pdfs", "logs"}
        album_dirs = [
            d for d in user_root.iterdir()
            if d.is_dir() and d.name not in exclude_dirs
        ]

        if len(album_dirs) <= self.max_albums:
            return

        album_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        to_delete = album_dirs[self.max_albums:]

        await self._run_sync(self._delete_album_folders, user_id, to_delete)

    def _delete_album_folders(self, user_id: str, folders: List[Path]):
        pdf_dir = self.global_base_dir / user_id / "pdfs"

        for folder in folders:
            album_id = folder.name
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                logger.info(f"已删除旧本子文件夹: {folder}")

            if pdf_dir.exists():
                pdf_file = pdf_dir / f"{album_id}.pdf"
                if pdf_file.exists():
                    pdf_file.unlink()
                    logger.info(f"已删除旧 PDF 文件: {pdf_file}")
                else:
                    for pf in pdf_dir.glob(f"{album_id}*.pdf"):
                        pf.unlink()
                        logger.info(f"已删除旧 PDF 文件: {pf}")

    # -------------------- 搜索 --------------------
    async def _do_search(self, event: AstrMessageEvent, keyword: str, page: int):
        try:
            option = await self._get_option(event.get_sender_id())
            client = option.build_jm_client()

            search_kwargs = {
                'search_query': keyword,
                'page': page,
                'main_tag': 0,
                'order_by': JmMagicConstants.ORDER_BY_LATEST,
                'time': JmMagicConstants.TIME_ALL,
                'category': JmMagicConstants.CATEGORY_ALL,
                'sub_category': None
            }

            search_page = await self._safe_call_with_timeout(
                client.search,
                timeout=30,
                **search_kwargs
            )

            if hasattr(search_page, 'content'):
                content = search_page.content
            else:
                content = list(search_page) if search_page else []

            if not content:
                await event.send(event.plain_result("没有找到相关本子"))
                return

            lines = [f"搜索「{keyword}」结果（第{page}/{getattr(search_page, 'page_count', 1)}页）："]
            for idx, item in enumerate(content[:10], 1):
                if isinstance(item, tuple) and len(item) == 2:
                    aid, info = item
                    title = info.get('name', '未知标题') if isinstance(info, dict) else str(info)
                elif isinstance(item, dict):
                    aid = item.get('id', '未知ID')
                    title = item.get('name', '未知标题')
                elif isinstance(item, str):
                    aid = item
                    title = '未知标题'
                else:
                    aid = str(item)
                    title = '未知标题'
                lines.append(f"{idx}. ID: {aid} | {title}")

            total = getattr(search_page, 'total', len(content))
            lines.append(f"共{total}条，当前第{page}页。使用 /jm detail <id> 查看详情")
            await event.send(event.plain_result("\n".join(lines)))
        except asyncio.TimeoutError:
            await event.send(event.plain_result("搜索超时，请稍后重试"))
        except Exception as e:
            logger.error(traceback.format_exc())
            await event.send(event.plain_result(f"搜索失败: {e}"))

    # -------------------- 排行榜 --------------------
    async def _do_ranking(self, event: AstrMessageEvent, rank_type: str, page: int):
        try:
            option = await self._get_option(event.get_sender_id())
            client = option.build_jm_client()

            if rank_type == "month":
                result = await self._safe_call_with_timeout(client.month_ranking, page=page)
            elif rank_type == "week":
                result = await self._safe_call_with_timeout(client.week_ranking, page=page)
            else:  # day
                result = await self._safe_call_with_timeout(client.day_ranking, page=page)

            if hasattr(result, 'content'):
                content = result.content
            else:
                content = list(result) if result else []

            if not content:
                await event.send(event.plain_result("暂无数据"))
                return

            lines = [f"{rank_type}榜 第{page}页："]
            for idx, item in enumerate(content[:10], 1):
                if isinstance(item, tuple) and len(item) == 2:
                    aid, info = item
                    title = info.get('name', '未知标题') if isinstance(info, dict) else str(info)
                elif isinstance(item, dict):
                    aid = item.get('id', '未知ID')
                    title = item.get('name', '未知标题')
                elif isinstance(item, str):
                    aid = item
                    title = '未知标题'
                else:
                    aid = str(item)
                    title = '未知标题'
                lines.append(f"{idx}. ID: {aid} | {title}")

            total = getattr(result, 'total', len(content))
            lines.append(f"共{total}条")
            await event.send(event.plain_result("\n".join(lines)))
        except Exception as e:
            logger.error(traceback.format_exc())
            await event.send(event.plain_result(f"获取排行榜失败: {e}"))

    # -------------------- 详情（含封面和标签） --------------------
    async def _do_detail(self, event: AstrMessageEvent, album_id: str):
        cover_path = None
        try:
            option = await self._get_option(event.get_sender_id())
            client = option.build_jm_client()
            album: JmAlbumDetail = await self._safe_call(client.get_album_detail, album_id)

            # 构造文本详情
            lines = [
                f"标题：{album.title}",
                f"作者：{album.author}",
                f"收藏数：{album.likes}",
                f"章节数：{len(album)}",
            ]
            if album.tags:
                tags_str = "、".join(album.tags)
                lines.append(f"标签：{tags_str}")
            else:
                lines.append("标签：无")

            if len(album) > 0:
                lines.append("章节列表：")
                for idx, photo in enumerate(album):
                    if idx >= 10:
                        lines.append(f"  ... 还有 {len(album)-10} 个章节")
                        break
                    lines.append(f"  {idx+1}. ID: {photo.photo_id} | {photo.name}")
            else:
                lines.append("该本子暂无章节")

            text_content = "\n".join(lines)

            # 创建节点内容列表
            node_content = [Plain(text_content)]

            # 尝试下载封面
            try:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    cover_path = tmp.name
                await self._run_sync(client.download_album_cover, album_id, cover_path)
                node_content.append(Image.fromFileSystem(cover_path))
            except Exception as e:
                logger.warning(f"下载封面失败: {e}")

            # 获取机器人自身 ID（用于转发消息中的发送者标识）
            bot_uin = event.get_self_id() or 10000
            # 使用固定名称，避免调用可能不存在的方法 get_self_nickname
            bot_name = "白咲"

            node = Node(
                uin=bot_uin,
                name=bot_name,
                content=node_content
            )

            yield event.chain_result([node])

        except Exception as e:
            await event.send(event.plain_result(f"获取详情失败: {e}"))
        finally:
            # 根据配置决定是否删除临时封面文件
            if cover_path and os.path.exists(cover_path) and self.delete_temp_cover:
                try:
                    os.unlink(cover_path)
                except Exception as e:
                    logger.warning(f"删除临时封面文件失败: {e}")

    # -------------------- 插件生命周期 --------------------
    async def terminate(self):
        logger.info("禁漫插件已卸载")