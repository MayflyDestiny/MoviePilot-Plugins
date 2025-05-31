import datetime
import threading
from typing import List, Tuple, Dict, Any, Optional

import pytz
from app.helper.sites import SitesHelper
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import ServiceInfo
from app.utils.string import StringUtils
# Added imports
from app.core.event import eventmanager, Event
from app.schemas.types import EventType


class TagMod(_PluginBase):
    # 插件名称
    plugin_name = "自动标签魔改版"
    # 插件描述
    plugin_desc = "给qb、tr的下载任务贴标签(支持自定义、魔改日志输出)"
    # 插件图标
    plugin_icon = "Youtube-dl_B.png"
    # 插件版本
    plugin_version = "1.2.2" # 如果功能有显著变化，可以考虑更新版本号
    # 插件作者
    plugin_author = "ClarkChen"
    # 作者主页
    author_url = "https://github.com/aClarkChen"
    # 插件配置项ID前缀
    plugin_config_prefix = "TagMod_"
    # 加载顺序
    plugin_order = 21
    # 可使用的用户级别
    auth_level = 2
    # 日志前缀
    LOG_TAG = "[Tag]"

    # 退出事件
    _event = threading.Event()
    # 私有属性
    sites_helper = None
    downloader_helper = None
    _scheduler = None
    _enabled = False
    _onlyonce = False
    _cover = False
    _site_first = False
    _interval = "计划任务"
    _interval_cron = "0 12 * * *"
    _interval_time = 24
    _interval_unit = "小时"
    _downloaders = None
    _tracker_map = "tracker地址:站点标签"
    _save_path_map = "保存地址:标签"

    def init_plugin(self, config: dict = None):
        self.sites_helper = SitesHelper()
        self.downloader_helper = DownloaderHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cover = config.get("cover")
            self._site_first = config.get("site_first")
            self._interval = config.get("interval") or "计划任务"
            self._interval_cron = config.get("interval_cron") or "0 12 * * *"
            self._interval_time = self.str_to_number(config.get("interval_time"), 24)
            self._interval_unit = config.get("interval_unit") or "小时"
            self._downloaders = config.get("downloaders")
            self._tracker_map = config.get("tracker_map") or "tracker地址:站点标签"
            self._save_path_map = config.get("save_path_map") or "保存地址:标签"

        # 停止现有任务
        self.stop_service()

        if self._onlyonce:
            # 创建定时任务控制器
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            # 执行一次, 关闭onlyonce
            self._onlyonce = False
            config.update({"onlyonce": self._onlyonce})
            self.update_config(config)
            # 启动自动标签
            self._scheduler.add_job(func=self._complemented_tags, trigger='date',
                                    run_date=datetime.datetime.now(
                                        tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                    )
            if self._scheduler and self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        if not self._downloaders:
            logger.warning(f"{self.LOG_TAG}尚未配置下载器，请检查配置")
            return None

        services = self.downloader_helper.get_services(name_filters=self._downloaders)
        if not services:
            logger.warning(f"{self.LOG_TAG}获取下载器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"{self.LOG_TAG}下载器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning(f"{self.LOG_TAG}没有已连接的下载器，请检查配置")
            return None

        return active_services

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled:
            if self._interval == "计划任务" or self._interval == "固定间隔":
                if self._interval == "固定间隔":
                    if self._interval_unit == "小时":
                        return [{
                            "id": "Tag",
                            "name": "自动补全标签",
                            "trigger": "interval",
                            "func": self._complemented_tags,
                            "kwargs": {
                                "hours": self._interval_time
                            }
                        }]
                    else:
                        if self._interval_time < 5:
                            self._interval_time = 5
                            logger.info(f"{self.LOG_TAG}启动定时服务: 最小不少于5分钟, 防止执行间隔太短任务冲突")
                        return [{
                            "id": "Tag",
                            "name": "自动补全标签",
                            "trigger": "interval",
                            "func": self._complemented_tags,
                            "kwargs": {
                                "minutes": self._interval_time
                            }
                        }]
                else:
                    return [{
                        "id": "Tag",
                        "name": "自动补全标签",
                        "trigger": CronTrigger.from_crontab(self._interval_cron),
                        "func": self._complemented_tags,
                        "kwargs": {}
                    }]
        return []

    @staticmethod
    def str_to_number(s: str, i: int) -> int:
        try:
            return int(s)
        except ValueError:
            return i

    def _complemented_tags(self):
        if not self.service_infos:
            return
        logger.info(f"{self.LOG_TAG}开始执行 ...")
        # 所有站点索引
        indexers_set = set(indexer.get("name") for indexer in self.sites_helper.get_indexers() if indexer.get("name"))

        parsed_tracker_map = {}
        if self._tracker_map and self._tracker_map != "tracker地址:站点标签":
            for item in self._tracker_map.splitlines():
                if ":" in item:
                    parts = item.split(":", 1)
                    if parts[0].strip() and parts[1].strip():
                        parsed_tracker_map[parts[0].strip()] = parts[1].strip()

        parsed_save_path_map = {}
        if self._save_path_map and self._save_path_map != "保存地址:标签":
            for item in self._save_path_map.splitlines():
                if ":" in item:
                    parts = item.split(":", 1)
                    if parts[0].strip() and parts[1].strip():
                        parsed_save_path_map[parts[0].strip()] = parts[1].strip()

        for service in self.service_infos.values():
            downloader = service.name
            downloader_obj = service.instance
            logger.info(f"{self.LOG_TAG}开始扫描下载器 {downloader} ...")
            if not downloader_obj: # Should be caught by service_infos active check, but good to have
                logger.error(f"{self.LOG_TAG} 获取下载器失败 {downloader}")
                continue
            # 获取下载器中的种子
            torrents, error = downloader_obj.get_torrents()
            # 如果下载器获取种子发生错误 或 没有种子 则跳过
            if error or not torrents:
                if error:
                    logger.error(f"{self.LOG_TAG}下载器 {downloader} 获取种子列表失败: {error}")
                else:
                    logger.info(f"{self.LOG_TAG}下载器 {downloader} 没有种子.")
                continue
            logger.info(f"{self.LOG_TAG}下载器 {downloader} 分析种子信息中 ({len(torrents)} 个种子)...")
            for torrent in torrents:
                try:
                    if self._event.is_set():
                        logger.info(f"{self.LOG_TAG}停止服务")
                        return
                    # 获取种子hash
                    _hash = self._get_hash(torrent=torrent, dl_type=service.type)
                    # 获取种子存储地址
                    _path = self._get_path(torrent=torrent, dl_type=service.type)
                    if not _hash or not _path:
                        logger.debug(f"{self.LOG_TAG}种子缺少 HASH ({_hash}) 或路径 ({_path})，跳过.")
                        continue
                        
                    torrent_labels_to_apply = []
                    # 1. 从保存路径应用标签
                    for key, label in parsed_save_path_map.items():
                        if key in _path:
                            torrent_labels_to_apply.append(label)
                            break 
                    
                    site_tag_from_rules = None
                    current_torrent_tags = self._get_tags(torrent=torrent, dl_type=service.type)

                    # 2. 确定是否需要添加站点标签
                    apply_tracker_based_site_tag = True
                    if not self._cover: # 如果不是覆盖模式
                        if indexers_set.intersection(set(current_torrent_tags)): # 检查现有标签是否已有站点标签
                            apply_tracker_based_site_tag = False 
                    
                    if apply_tracker_based_site_tag:
                        trackers = self._get_trackers(torrent=torrent, dl_type=service.type)
                        for tracker_url in trackers:
                            # 先从自定义tracker map找
                            for key, label in parsed_tracker_map.items():
                                if key in tracker_url:
                                    site_tag_from_rules = label
                                    break
                            if site_tag_from_rules:
                                break
                            # 再从站点助手根据域名找
                            domain = StringUtils.get_url_domain(tracker_url)
                            site_info = self.sites_helper.get_indexer(domain)
                            if site_info:
                                site_tag_from_rules = site_info.get("name")
                                break
                        if site_tag_from_rules and site_tag_from_rules not in torrent_labels_to_apply:
                            torrent_labels_to_apply.append(site_tag_from_rules)

                    original_tags_for_api = current_torrent_tags
                    if self._cover:
                        if service.type == "qbittorrent" and current_torrent_tags and any(t.strip() for t in current_torrent_tags):
                             downloader_obj.qbc.torrents_remove_tags(torrent_hashes=_hash, tags=current_torrent_tags)
                        original_tags_for_api = [] # 空列表表示覆盖模式下没有“原始”标签去比较差异

                    if torrent_labels_to_apply:
                        unique_labels_to_apply = list(dict.fromkeys(torrent_labels_to_apply)) # 保持顺序去重
                        self._set_torrent_info(service=service, _hash=_hash, _tags=unique_labels_to_apply, _original_tags=original_tags_for_api)
                except Exception as e:
                    logger.error(
                        f"{self.LOG_TAG}分析种子信息时发生了错误 (Hash: {_hash if '_hash' in locals() else 'N/A'}): {str(e)}", exc_info=True)
        logger.info(f"{self.LOG_TAG}执行完成")

    @eventmanager.register(EventType.DownloadAdded)
    def download_added(self, event: Event):
        if not self.get_state() or not self._enabled:
            return

        if not event.event_data:
            logger.debug(f"{self.LOG_TAG}DownloadAdded event missing data.")
            return

        try:
            downloader_name = event.event_data.get("downloader")
            _hash = event.event_data.get("hash")

            if not downloader_name or not _hash:
                logger.info(f"{self.LOG_TAG}DownloadAdded event missing downloader name or hash.")
                return

            if not self.service_infos:
                logger.warning(f"{self.LOG_TAG}No active downloaders configured for DownloadAdded event.")
                return
            
            service = self.service_infos.get(downloader_name)
            if not service:
                logger.info(f"{self.LOG_TAG}Downloader {downloader_name} not managed by this plugin or not active, skipping for DownloadAdded.")
                return

            downloader_obj = service.instance
            torrents_data, error = downloader_obj.get_torrents(ids=_hash)
            if error or not torrents_data:
                logger.error(f"{self.LOG_TAG}Failed to fetch torrent info for hash {_hash} from {downloader_name} on DownloadAdded: {error or 'Not found'}")
                return
            
            torrent = torrents_data[0]

            _path = self._get_path(torrent=torrent, dl_type=service.type)
            if not _path:
                logger.debug(f"{self.LOG_TAG}No save path found for torrent {_hash} on DownloadAdded.")
                # Path might not be critical for all tagging rules, so we continue

            # 解析映射表 (与 _complemented_tags 保持一致)
            parsed_tracker_map = {}
            if self._tracker_map and self._tracker_map != "tracker地址:站点标签":
                for item in self._tracker_map.splitlines():
                    if ":" in item:
                        parts = item.split(":", 1)
                        if parts[0].strip() and parts[1].strip():
                            parsed_tracker_map[parts[0].strip()] = parts[1].strip()
            
            parsed_save_path_map = {}
            if self._save_path_map and self._save_path_map != "保存地址:标签":
                for item in self._save_path_map.splitlines():
                    if ":" in item:
                        parts = item.split(":", 1)
                        if parts[0].strip() and parts[1].strip():
                             parsed_save_path_map[parts[0].strip()] = parts[1].strip()

            torrent_labels_to_apply = []
            # 1. 从保存路径应用标签
            if _path: # Ensure path exists before trying to match
                for key, label in parsed_save_path_map.items():
                    if key in _path:
                        torrent_labels_to_apply.append(label)
                        break 
            
            site_tag_from_rules = None
            current_torrent_tags = self._get_tags(torrent=torrent, dl_type=service.type)
            
            # 2. 确定是否需要添加站点标签
            apply_tracker_based_site_tag = True
            if not self._cover: # 如果不是覆盖模式
                # 动态获取最新的站点列表用于比较
                indexers_set = set(indexer.get("name") for indexer in self.sites_helper.get_indexers() if indexer.get("name"))
                if indexers_set.intersection(set(current_torrent_tags)): # 检查现有标签是否已有站点标签
                    apply_tracker_based_site_tag = False 
            
            if apply_tracker_based_site_tag:
                trackers = self._get_trackers(torrent=torrent, dl_type=service.type)
                for tracker_url in trackers:
                    for key, label in parsed_tracker_map.items():
                        if key in tracker_url:
                            site_tag_from_rules = label
                            break
                    if site_tag_from_rules:
                        break
                    domain = StringUtils.get_url_domain(tracker_url)
                    site_info = self.sites_helper.get_indexer(domain)
                    if site_info:
                        site_tag_from_rules = site_info.get("name")
                        break
                if site_tag_from_rules and site_tag_from_rules not in torrent_labels_to_apply:
                    torrent_labels_to_apply.append(site_tag_from_rules)

            original_tags_for_api = current_torrent_tags
            if self._cover:
                if service.type == "qbittorrent" and current_torrent_tags and any(t.strip() for t in current_torrent_tags): # Check if list is not empty and contains non-whitespace tags
                     downloader_obj.qbc.torrents_remove_tags(torrent_hashes=_hash, tags=current_torrent_tags)
                original_tags_for_api = []

            if torrent_labels_to_apply:
                unique_labels_to_apply = list(dict.fromkeys(torrent_labels_to_apply)) # 保持顺序去重
                self._set_torrent_info(service=service, 
                                       _hash=_hash, 
                                       _tags=unique_labels_to_apply,
                                       _original_tags=original_tags_for_api)
            else:
                logger.info(f"{self.LOG_TAG}No new tags to apply for torrent {_hash} on DownloadAdded event.")

        except Exception as e:
            logger.error(f"{self.LOG_TAG}Error processing DownloadAdded event (Hash: {_hash if '_hash' in locals() else 'N/A'}): {str(e)}", exc_info=True)


    @staticmethod
    def _get_hash(torrent: Any, dl_type: str):
        try:
            return torrent.get("hash") if dl_type == "qbittorrent" else torrent.hashString
        except Exception as e:
            # Consider logging here if this is unexpected, but print might be for dev
            logger.error(f"Error getting hash: {str(e)}")
            return ""

    @staticmethod
    def _get_path(torrent: Any, dl_type: str):
        try:
            return torrent.get("save_path") if dl_type == "qbittorrent" else torrent.download_dir
        except Exception as e:
            logger.error(f"Error getting path: {str(e)}")
            return ""

    @staticmethod
    def _get_trackers(torrent: Any, dl_type: str):
        try:
            if dl_type == "qbittorrent":
                return [tracker.get("url") for tracker in (torrent.trackers or []) if tracker.get("tier", -1) >= 0 and tracker.get("url")]
            else: # transmission-rpc typically returns a list of lists/dicts for trackers, ensure compatibility
                # Assuming torrent.trackers is a list of objects each having an 'announce' and 'tier'
                return [tracker.announce for tracker in (torrent.trackers or []) if hasattr(tracker, 'announce') and hasattr(tracker, 'tier') and tracker.tier >= 0 and tracker.announce]
        except Exception as e:
            logger.error(f"Error getting trackers: {str(e)}")
            return []

    @staticmethod
    def _get_tags(torrent: Any, dl_type: str):
        try:
            if dl_type == "qbittorrent":
                tags_str = torrent.get("tags", "")
                return [str(tag).strip() for tag in tags_str.split(',') if str(tag).strip()] if tags_str else []
            else: # transmission-rpc Torrent object has 'labels' attribute which is a list
                return torrent.labels if hasattr(torrent, 'labels') and torrent.labels else []
        except Exception as e:
            logger.error(f"Error getting tags: {str(e)}")
            return []

    def _set_torrent_info(self, service: ServiceInfo, _hash: str, _tags=None, _original_tags: list = None):
        if not service or not service.instance:
            return
        downloader_obj = service.instance
        
        tags_to_log_and_set = [] # Default to empty list

        if _tags is None: # Ensure _tags is a list for processing
            _tags = []

        if service.type == "qbittorrent":
            actual_tags_to_manipulate = list(set(_tags)) # Use unique tags from rules
            if not self._cover and _original_tags is not None:
                # Add mode: find tags that are in _tags but not in _original_tags
                tags_to_add = list(set(actual_tags_to_manipulate) - set(_original_tags))
                if tags_to_add:
                    downloader_obj.qbc.torrents_add_tags(torrent_hashes=_hash, tags=tags_to_add)
                    tags_to_log_and_set = tags_to_add
            else: # Cover mode or no original tags to compare against
                # Set mode: replace all tags with actual_tags_to_manipulate
                # If _cover was true, existing tags were already removed in calling function for qB.
                # If not _cover but _original_tags was None/empty, this effectively sets.
                downloader_obj.qbc.torrents_set_tags(torrent_hashes=_hash, tags=actual_tags_to_manipulate)
                tags_to_log_and_set = actual_tags_to_manipulate
        else: # Transmission, etc.
            # Transmission's API for setting labels usually replaces them.
            # If we need to merge, it has to be done before calling.
            effective_tags_for_tr = list(set(_tags)) # Start with unique rule tags

            if not self._cover and _original_tags is not None:
                # Merge new tags with original ones for TR add mode
                effective_tags_for_tr = list(set(_original_tags + effective_tags_for_tr))
            
            if self._site_first and self._cover : # Only apply site_first reverse for TR in cover mode
                 # This implies the list `effective_tags_for_tr` should have site tag last to make it first after reverse
                 # The current build order is [path_label, site_label]. Reversed: [site_label, path_label]
                 # So, if site_first means site comes first, this is correct.
                effective_tags_for_tr = effective_tags_for_tr[::-1]

            try:
                # Assuming downloader_obj.trc for transmission client
                downloader_obj.trc.change_torrent(ids=[_hash], labels=effective_tags_for_tr)
                tags_to_log_and_set = effective_tags_for_tr # Log what was attempted to set
            except Exception as e:
                 logger.error(f"{self.LOG_TAG}下载器: {service.name} 种子id: {_hash} 设置标签失败: {str(e)}")


        if tags_to_log_and_set: # Log only if some tags were actually intended to be set/added
            logger.warn(f"{self.LOG_TAG}下载器: {service.name} 种子id: {_hash}   标签: {','.join(tags_to_log_and_set)}")


    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'cover',
                                            'label': '覆盖模式',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'site_first',
                                            'label': '站点优先(TR覆盖)', # Clarified label
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VCheckboxBtn',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '运行一次'
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'downloaders',
                                            'label': '下载器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in self.downloader_helper.get_configs().values()]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'interval',
                                            'label': '定时任务',
                                            'items': [
                                                {'title': '禁用', 'value': '禁用'},
                                                {'title': '计划任务', 'value': '计划任务'},
                                                {'title': '固定间隔', 'value': '固定间隔'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'interval_cron',
                                            'label': '计划任务设置',
                                            'placeholder': '0 12 * * *'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'interval_time',
                                            'label': '时间间隔, 每',
                                            'placeholder': '24'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'interval_unit',
                                            'label': '单位',
                                            'items': [
                                                {'title': '小时', 'value': '小时'},
                                                {'title': '分钟', 'value': '分钟'}
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "tracker_map",
                                            "label": "tracker网址:站点标签",
                                            "rows": 5,
                                            "placeholder": "如:tracker.XXX:XX",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "save_path_map",
                                            "label": "保存地址:标签",
                                            "rows": 5,
                                            "placeholder": "如:/volume1/XX保种/:XX保种\n/volume1/保种/:保种",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '每行配置一个，只会匹配一个，行数越高优先级越高。注意！！需用英文的:。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "cover": False,
            "site_first": False,
            "interval": "计划任务",
            "interval_cron": "0 12 * * *",
            "interval_time": "24",
            "interval_unit": "小时",
            "tracker_map": "tracker地址:站点标签",
            "save_path_map": "保存地址:标签"
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            # Consider logging here
            logger.error(f"{self.LOG_TAG}Error stopping service: {str(e)}")