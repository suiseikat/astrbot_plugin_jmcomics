import asyncio
import functools
import os
import shutil
import tempfile
import traceback
import zipfile
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
from astrbot.api.message_components import Plain, File, Node, Image

# 尝试导入 jmcomic，若失败则标记并后续提示
try:
    import jmcomic
    from jmcomic import (
        JmOption,
        JmAlbumDetail,
        JmModuleConfig,
        JmcomicException,
        MissingAlbumPhotoException,
        RequestRetryAllFailException,
        create_option_by_file,
        download_album,
        DirRule,
        ExceptionTool,
        time_stamp,
        current_thread,
        fix_windir_name,
    )
    from jmcomic import JmMagicConstants
    from jmcomic.jm_downloader import JmDownloader
    JMCOMIC_AVAILABLE = True
except ImportError as e:
    JMCOMIC_AVAILABLE = False
    logger.error(f"导入 jmcomic 失败: {e}，请手动安装: pip install jmcomic")

# 尝试导入 PDF 相关库，使用别名避免与 AstrBot 消息组件的 Image 冲突
try:
    import img2pdf
    from PIL import Image as PILImage
    PDF_AVAILABLE = True
except ImportError as e:
    PDF_AVAILABLE = False
    logger.error(f"导入 img2pdf 或 Pillow 失败: {e}，请手动安装: pip install img2pdf Pillow")

# -------------------- 插件配置默认值 --------------------
DEFAULT_OPTION_FILE = Path(__file__).parent / "assets" / "option" / "option_workflow_download.yml"


@register("jmcomic_downloader", "JMComic 下载", "禁漫下载插件（支持范围下载、图文详情、智能清理）", "2.9.2")
class JmComicPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 获取插件专属数据目录
        data_dir = StarTools.get_data_dir("jmcomic")
        self.global_base_dir = Path(self.config.get("download_dir", data_dir))
        try:
            self.global_base_dir.mkdir(parents=True, exist_ok=True)
            # 测试写入权限
            test_file = self.global_base_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            logger.error(f"下载目录 {self.global_base_dir} 不可写，将使用系统临时目录")
            self.global_base_dir = Path(tempfile.gettempdir()) / "jmcomic_downloads"
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

        # 检查核心库是否可用
        self.jmcomic_available = JMCOMIC_AVAILABLE
        self.pdf_available = PDF_AVAILABLE

        if not self.jmcomic_available:
            logger.error("jmcomic 库不可用，插件功能将受限")

        # 禁用 jmcomic 内部日志（如果可用）
        if self.jmcomic_available and not self.config.get("enable_jm_log", False):
            JmModuleConfig.disable_jm_log()

        # 安全地启动预热（避免无事件循环错误）
        self._need_warmup = True
        if self.jmcomic_available:
            try:
                asyncio.create_task(self._warmup())
                self._need_warmup = False
            except RuntimeError:
                logger.warning("当前无运行中事件循环，预热任务推迟到首次请求时执行")

    async def _warmup(self):
        if not self.jmcomic_available:
            return
        try:
            await asyncio.to_thread(JmModuleConfig.get_html_domain)
            self._need_warmup = False
        except Exception as e:
            logger.warning(f"预热域名失败: {e}")

    # -------------------- 路径安全处理 --------------------
    def _safe_user_dir(self, user_id: str) -> str:
        """将用户 ID 转换为安全的目录名（只保留字母数字下划线）"""
        safe = re.sub(r'[^a-zA-Z0-9_-]', '_', user_id)
        return safe or "unknown_user"

    # -------------------- 辅助方法 --------------------
    async def _get_option(self, user_id: str = None, cmd_overrides: dict = None) -> Optional[JmOption]:
        if not self.jmcomic_available:
            return None

        # 如果需要预热且尚未预热，立即执行预热
        if self._need_warmup:
            asyncio.create_task(self._warmup())  # 不等待

        try:
            if self.option_file and Path(self.option_file).exists():
                option = await asyncio.to_thread(create_option_by_file, self.option_file)
            else:
                option = await asyncio.to_thread(JmOption.default)

            if user_id:
                safe_id = self._safe_user_dir(user_id)
                user_dir = self.global_base_dir / safe_id
            else:
                user_dir = self.global_base_dir
            option.dir_rule.base_dir = str(user_dir)

            if cmd_overrides:
                self._apply_overrides(option, cmd_overrides)

            return option
        except Exception as e:
            logger.error(f"创建 JmOption 失败: {e}")
            return None

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
        if not self.jmcomic_available:
            raise Exception("jmcomic 库未正确安装")
        try:
            return await self._run_sync(func, *args, **kwargs)
        except MissingAlbumPhotoException as e:
            raise Exception(f"本子/章节不存在: {e}") from e
        except RequestRetryAllFailException as e:
            raise Exception(f"请求重试失败，请稍后重试: {e}") from e
        except JmcomicException as e:
            raise Exception(f"jmcomic 错误: {e}") from e
        except Exception as e:
            logger.error(traceback.format_exc())
            raise Exception(f"未知错误: {e}") from e

    async def _safe_call_with_timeout(self, func, timeout=30, *args, **kwargs):
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, functools.partial(func, *args, **kwargs)),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.error(traceback.format_exc())
            raise

    # -------------------- 创建范围下载器 --------------------
    def _create_range_downloader(self, start: int, end: int):
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

    # -------------------- 命令解析（支持范围、压缩参数） --------------------
    def _parse_album_command(self, args: List[str]) -> Tuple[str, Optional[Tuple[int, int]], Dict[str, Any]]:
        album_id = args[1]
        start = end = None
        extra = {}
        i = 2
        while i < len(args):
            arg = args[i]
            if arg.startswith('--'):
                if '=' in arg:
                    key, value = arg[2:].split('=', 1)
                    extra[key] = value
                elif i + 1 < len(args) and not args[i+1].startswith('--'):
                    extra[arg[2:]] = args[i+1]
                    i += 1
                else:
                    extra[arg[2:]] = True
            else:
                # 范围参数
                if '-' in arg:
                    parts = arg.split('-')
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        start = int(parts[0])
                        end = int(parts[1])
                elif arg.isdigit():
                    start = end = int(arg)
            i += 1
        return album_id, (start, end) if start is not None else None, extra

    # -------------------- 命令 --------------------
    @filter.command("jm download")
    async def command_jm_download(self, event: AstrMessageEvent):
        """下载本子，支持范围选择，生成PDF（支持 --quality 和 --max-size 压缩参数）"""
        if not self.jmcomic_available:
            yield event.plain_result("jmcomic 库未正确安装，无法使用下载功能")
            return
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供本子ID，例如：/jm download 123")
            return
        album_id, range_tuple, extra = self._parse_album_command(args)
        overrides = {}
        if range_tuple:
            overrides['chapter_range'] = range_tuple
            start, end = range_tuple
            yield event.plain_result(f"开始下载本子 {album_id} 第{start}~{end}章，请稍候...")
        else:
            yield event.plain_result(f"开始下载本子 {album_id}，请稍候...")
        asyncio.create_task(self._download_album_task(event, album_id, pack=False, overrides=overrides, extra=extra))

    @filter.command("jmz")
    async def command_jmz(self, event: AstrMessageEvent):
        """下载本子并打包为ZIP"""
        if not self.jmcomic_available:
            yield event.plain_result("jmcomic 库未正确安装，无法使用下载功能")
            return
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result("请提供本子ID，例如：/jmz 123")
            return
        album_id, range_tuple, extra = self._parse_album_command(args)
        overrides = {}
        if range_tuple:
            overrides['chapter_range'] = range_tuple
            start, end = range_tuple
            yield event.plain_result(f"开始下载本子 {album_id} 第{start}~{end}章（ZIP打包），请稍候...")
        else:
            yield event.plain_result(f"开始下载本子 {album_id}（ZIP打包），请稍候...")
        asyncio.create_task(self._download_album_task(event, album_id, pack=True, overrides=overrides, extra=extra))

    @filter.command("jms")
    async def command_jms(self, event: AstrMessageEvent):
        """搜索本子"""
        if not self.jmcomic_available:
            yield event.plain_result("jmcomic 库未正确安装，无法使用搜索功能")
            return
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
        """获取排行榜（默认月榜）"""
        if not self.jmcomic_available:
            yield event.plain_result("jmcomic 库未正确安装，无法使用排行榜功能")
            return
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
        """查看本子详情（含封面和标签）"""
        if not self.jmcomic_available:
            yield event.plain_result("jmcomic 库未正确安装，无法查看详情")
            return
        args = event.message_str.strip().split()
        if len(args) < 3:
            yield event.plain_result("请提供本子ID，例如：/jm detail 123")
            return
        album_id = args[2]
        async for ret in self._do_detail(event, album_id):
            yield ret

    @filter.command("jm help")
    async def command_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
【禁漫下载插件使用说明】

/jm download <本子号> [范围] [--quality=80] [--max-size=1920]  
    下载本子指定范围章节（默认全部），生成PDF发送。范围示例：1-10 或 5
    压缩参数：--quality 图片质量（1-100，默认85），--max-size 最大边长（默认不限制）
/jmz <本子号> [范围]         下载本子并打包ZIP发送
/jms <关键词> [页码]         搜索本子，默认第1页
/jmr [week|day] [页码]       排行榜，默认月榜第1页
/jm detail <本子号>          查看本子详情（含封面和标签）
/jm help                     显示本帮助信息

清理模式可在插件配置中修改。
        """.strip()
        yield event.plain_result(help_text)

    # -------------------- PDF 生成（带压缩） --------------------
    async def _generate_compressed_pdf(self, image_dir: Path, output_pdf: Path, quality: int = 85, max_size: int = 0) -> bool:
        """
        将目录下的所有图片压缩后合并为 PDF
        :param image_dir: 图片目录（包含所有图片）
        :param output_pdf: 输出 PDF 路径
        :param quality: JPEG 压缩质量 (1-100)
        :param max_size: 最大边长限制（0 表示不限制）
        :return: 成功返回 True
        """
        if not self.pdf_available:
            logger.error("PDF 库未安装，无法生成 PDF")
            return False

        # 获取所有图片文件（按文件名排序）
        try:
            image_files = sorted(
                [f for f in image_dir.glob("*") if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp')]
            )
        except Exception as e:
            logger.error(f"扫描图片目录失败: {e}")
            return False

        if not image_files:
            logger.warning("没有找到图片")
            return False

        # 创建临时目录存放压缩后的图片
        tmpdir_obj = tempfile.TemporaryDirectory(prefix="jm_pdf_")
        tmpdir = tmpdir_obj.name
        try:
            tmp_paths = []
            for img_path in image_files:
                try:
                    img = PILImage.open(img_path)
                    # 转换为 RGB（PDF 需要）
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    # 缩放尺寸（如果指定了 max_size）
                    if max_size > 0:
                        img.thumbnail((max_size, max_size), PILImage.Resampling.LANCZOS)
                    # 保存为 JPEG 格式到临时目录
                    out_name = img_path.stem + ".jpg"
                    out_path = Path(tmpdir) / out_name
                    img.save(out_path, "JPEG", quality=quality, optimize=True)
                    tmp_paths.append(str(out_path))
                except Exception as e:
                    logger.error(f"压缩图片失败 {img_path}: {e}")
                    return False

            # 使用 img2pdf 合并为 PDF
            try:
                with open(output_pdf, "wb") as f:
                    f.write(img2pdf.convert(tmp_paths))
                return True
            except Exception as e:
                logger.error(f"生成 PDF 失败: {e}")
                return False
        finally:
            # 清理临时目录
            try:
                tmpdir_obj.cleanup()
            except Exception as e:
                logger.warning(f"清理临时目录失败: {e}")

    # -------------------- 下载任务 --------------------
    async def _download_album_task(self, event: AstrMessageEvent, album_id: str, pack: bool, overrides: dict, extra: dict):
        user_id = event.get_sender_id()
        sent_files = []  # 记录发送的文件，用于清理
        try:
            option = await self._get_option(user_id, overrides)
            if option is None:
                await event.send(event.plain_result("无法创建下载配置，请检查日志"))
                return

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
                zip_path = await self._handle_zip_result(event, album_id, album_dir)
                if zip_path:
                    sent_files.append(zip_path)
            else:
                # 解析压缩参数
                quality = int(extra.get('quality', 85))
                max_size = int(extra.get('max-size', 0))
                # 限制 quality 范围
                quality = max(1, min(100, quality))

                pdf_dir = self.global_base_dir / self._safe_user_dir(user_id) / "pdfs"
                try:
                    pdf_dir.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    logger.error(f"创建 PDF 目录失败: {e}")
                    await event.send(event.plain_result("无法创建 PDF 目录"))
                    return

                pdf_path = pdf_dir / f"{album_id}.pdf"

                # 生成压缩后的 PDF
                success = await self._generate_compressed_pdf(album_dir, pdf_path, quality, max_size)
                if success:
                    await event.send(event.chain_result([
                        Plain(f"本子 {album_id} 下载完成，已转换为 PDF（质量={quality}, 最大尺寸={max_size or '原始'}）："),
                        File(file=str(pdf_path), name=pdf_path.name)
                    ]))
                    sent_files.append(pdf_path)
                else:
                    await event.send(event.plain_result("PDF 生成失败"))

            # 清理逻辑
            if sent_files and self.cleanup_mode == "after_send":
                asyncio.create_task(self._delete_after_send(album_dir, sent_files))
            elif self.cleanup_mode == "count":
                asyncio.create_task(self._cleanup_old_albums(user_id))

        except Exception as e:
            logger.error(f"下载任务异常: {traceback.format_exc()}")
            await event.send(event.plain_result(f"下载失败: {e}"))

    async def _handle_zip_result(self, event: AstrMessageEvent, item_id: str, folder: Path) -> Optional[Path]:
        if not folder.exists() or not any(folder.iterdir()):
            await event.send(event.plain_result("下载完成但文件夹为空，可能未成功下载任何图片。"))
            return None

        zip_path = folder.with_suffix(".zip")
        try:
            await self._run_sync(self._zip_folder, folder, zip_path)
            if zip_path.exists():
                await event.send(event.chain_result([
                    Plain(f"ID {item_id} 下载完成，打包文件："),
                    File(file=str(zip_path), name=zip_path.name)
                ]))
                return zip_path
            else:
                await event.send(event.plain_result("打包失败"))
                return None
        except Exception as e:
            logger.error(f"压缩失败: {e}")
            await event.send(event.plain_result("打包失败"))
            return None

    @staticmethod
    def _zip_folder(folder: Path, zip_path: Path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, start=folder.parent)
                    zipf.write(file_path, arcname)

    async def _delete_after_send(self, album_dir: Path, sent_files: List[Path]):
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

    async def _cleanup_old_albums(self, user_id: str):
        if self.cleanup_mode != "count" or self.max_albums <= 0:
            return
        safe_id = self._safe_user_dir(user_id)
        user_root = self.global_base_dir / safe_id
        if not user_root.exists():
            return
        exclude_dirs = {"pdfs", "logs"}
        try:
            album_dirs = [d for d in user_root.iterdir() if d.is_dir() and d.name not in exclude_dirs]
        except Exception as e:
            logger.error(f"读取用户目录失败: {e}")
            return
        if len(album_dirs) <= self.max_albums:
            return
        album_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        to_delete = album_dirs[self.max_albums:]
        await self._run_sync(self._delete_album_folders, user_id, to_delete)

    def _delete_album_folders(self, user_id: str, folders: List[Path]):
        safe_id = self._safe_user_dir(user_id)
        pdf_dir = self.global_base_dir / safe_id / "pdfs"
        for folder in folders:
            album_id = folder.name
            # 删除文件夹
            if folder.exists():
                try:
                    shutil.rmtree(folder, ignore_errors=True)
                    logger.info(f"已删除旧本子文件夹: {folder}")
                except Exception as e:
                    logger.error(f"删除文件夹失败 {folder}: {e}")
            # 删除对应的 PDF 文件
            if pdf_dir.exists():
                try:
                    for pf in pdf_dir.glob(f"{album_id}*.pdf"):
                        pf.unlink()
                        logger.info(f"已删除旧 PDF 文件: {pf}")
                except Exception as e:
                    logger.error(f"删除 PDF 文件失败: {e}")
            # 删除对应的 ZIP 文件（同级目录）
            zip_file = folder.with_suffix(".zip")
            if zip_file.exists():
                try:
                    zip_file.unlink()
                    logger.info(f"已删除旧 ZIP 文件: {zip_file}")
                except Exception as e:
                    logger.error(f"删除 ZIP 文件失败: {e}")

    # -------------------- 搜索 --------------------
    async def _do_search(self, event: AstrMessageEvent, keyword: str, page: int):
        try:
            option = await self._get_option(event.get_sender_id())
            if option is None:
                await event.send(event.plain_result("无法创建下载配置"))
                return
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
            search_page = await self._safe_call_with_timeout(client.search, timeout=30, **search_kwargs)
            content = search_page.content if hasattr(search_page, 'content') else list(search_page) if search_page else []
            if not content:
                await event.send(event.plain_result("没有找到相关本子"))
                return
            lines = [f"搜索「{keyword}」结果（第{page}/{getattr(search_page, 'page_count', 1)}页）："]
            for idx, (aid, info) in enumerate(content[:10], 1):
                title = info.get('name', '未知标题') if isinstance(info, dict) else str(info)
                lines.append(f"{idx}. ID: {aid} | {title}")
            lines.append(f"共{getattr(search_page, 'total', len(content))}条，当前第{page}页。")
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
            if option is None:
                await event.send(event.plain_result("无法创建下载配置"))
                return
            client = option.build_jm_client()
            if rank_type == "month":
                result = await self._safe_call_with_timeout(client.month_ranking, page=page)
            elif rank_type == "week":
                result = await self._safe_call_with_timeout(client.week_ranking, page=page)
            else:
                result = await self._safe_call_with_timeout(client.day_ranking, page=page)
            content = result.content if hasattr(result, 'content') else list(result) if result else []
            if not content:
                await event.send(event.plain_result("暂无数据"))
                return
            lines = [f"{rank_type}榜 第{page}页："]
            for idx, (aid, info) in enumerate(content[:10], 1):
                title = info.get('name', '未知标题') if isinstance(info, dict) else str(info)
                lines.append(f"{idx}. ID: {aid} | {title}")
            lines.append(f"共{len(content)}条")
            await event.send(event.plain_result("\n".join(lines)))
        except Exception as e:
            logger.error(traceback.format_exc())
            await event.send(event.plain_result(f"获取排行榜失败: {e}"))

    # -------------------- 详情（含封面和标签） + 延迟删除封面 --------------------
    async def _delayed_delete(self, path: Path, delay: int):
        """延迟删除文件，给消息发送留出时间"""
        await asyncio.sleep(delay)
        try:
            if path.exists():
                path.unlink()
                logger.debug(f"已删除临时封面: {path}")
        except Exception as e:
            logger.warning(f"删除临时封面失败: {e}")

    async def _do_detail(self, event: AstrMessageEvent, album_id: str):
        cover_path = None
        try:
            option = await self._get_option(event.get_sender_id())
            if option is None:
                await event.send(event.plain_result("无法创建下载配置"))
                return
            client = option.build_jm_client()
            album: JmAlbumDetail = await self._safe_call(client.get_album_detail, album_id)

            lines = [
                f"标题：{album.title}",
                f"作者：{album.author}",
                f"收藏数：{album.likes}",
                f"章节数：{len(album)}",
            ]
            if album.tags:
                lines.append(f"标签：{'、'.join(album.tags)}")
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

            node_content = [Plain("\n".join(lines))]

            # 下载封面（使用持久化临时目录 + 延迟删除）
            try:
                temp_cover_dir = self.global_base_dir / "temp_covers"
                temp_cover_dir.mkdir(parents=True, exist_ok=True)
                cover_filename = f"cover_{album_id}_{int(time.time())}.jpg"
                cover_path = temp_cover_dir / cover_filename

                await self._run_sync(client.download_album_cover, album_id, str(cover_path))
                node_content.append(Image.fromFileSystem(str(cover_path)))

                # 延迟 60 秒后删除，确保消息发送完成
                asyncio.create_task(self._delayed_delete(cover_path, 60))
            except Exception as e:
                logger.warning(f"下载封面失败: {e}")

            bot_uin = event.get_self_id() or 10000
            node = Node(uin=bot_uin, name="JMComic Bot", content=node_content)
            yield event.chain_result([node])

        except Exception as e:
            await event.send(event.plain_result(f"获取详情失败: {e}"))
        # 不再需要 finally 删除，由延迟任务处理

    # -------------------- 插件生命周期 --------------------
    async def terminate(self):
        logger.info("禁漫插件已卸载")
