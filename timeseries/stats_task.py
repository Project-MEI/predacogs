"""
MIT License

Copyright (c) 2019-2021 Predä

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import concurrent
import contextlib
import functools
import logging
import re
from collections import Counter, defaultdict
from types import SimpleNamespace
from typing import Any, Mapping

import aiohttp
import discord
import lavalink
from aiohttp import ClientTimeout
from redbot.core import bank
from redbot.core.bot import Red
from redbot.core.utils import AsyncIter

from timeseries.setting_cache import SettingCacheManager

log = logging.getLogger("red.predacogs.stats_tasks")


vc_regions = {
    "eu-west": "EU West",
    "eu-central": "EU Central",
    "europe": "Europe",
    "london": "London",
    "frankfurt": "Frankfurt",
    "amsterdam": "Amsterdam",
    "us-west": "US West",
    "us-east": "US East",
    "us-south": "US South",
    "us-central": "US Central",
    "singapore": "Singapore",
    "sydney": "Sydney",
    "brazil": "Brazil",
    "hongkong": "Hong Kong",
    "russia": "Russia",
    "japan": "Japan",
    "southafrica": "South Africa",
    "india": "India",
    "dubai": "Dubai",
    "south-korea": "South Korea",
}

verify = {
    "none": "None",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "extreme": "Extreme",
}
features = {
    "VIP_REGIONS": "VIP Voice Servers",
    "VANITY_URL": "Vanity URL",
    "INVITE_SPLASH": "Splash Invite",
    "VERIFIED": "Verified",
    "PARTNERED": "Partnered",
    "MORE_EMOJI": "More Emojis",
    "DISCOVERABLE": "Server Discovery",
    "FEATURABLE": "Featurable",
    "COMMERCE": "Commerce",
    "PUBLIC": "Public",
    "NEWS": "News Channels",
    "BANNER": "Banner Image",
    "ANIMATED_ICON": "Animated Icon",
    "PUBLIC_DISABLED": "Public disabled",
    "MEMBER_LIST_DISABLED": "Member list disabled",
    "ENABLED_DISCOVERABLE_BEFORE": "Was in Server Discovery",
    "WELCOME_SCREEN_ENABLED": "Welcome Screen",
}


def call_sync_as_async(function, *args, **kwargs) -> Any:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        for future in concurrent.futures.as_completed(
            [executor.submit(function, *args, **kwargs)]
        ):
            return future.result()


async def write_bot_data(bot: Red, config_cache: SettingCacheManager):
    lightmode = await config_cache.get_set_lightmode()
    if not lightmode:
        detailed = await config_cache.get_set_detailed()
        topgg = await config_cache.get_set_topgg()
    else:
        detailed = False
        topgg = False

    try:
        counter = Counter()
        server_counter = Counter()
        region_count = Counter()
        verify_count = Counter()
        features_count = Counter()
        temp_data = defaultdict(set)
        server_temp_data = defaultdict(set)

        if topgg:
            vote_data = await get_votes(bot) or {}
            if vote_data:
                if vote_data.get("monthlyPoints"):
                    counter["Monthly Votes"] = vote_data["monthlyPoints"]
                if vote_data.get("points"):
                    counter["Votes"] = vote_data["points"]
        server_counter["Total"] = len(bot.guilds)
        with contextlib.suppress(OverflowError):
            counter["Discord Latency"] = int(round(bot.latency * 1000))
        counter["Shards"] = bot.shard_count
        for guild in bot.guilds:
            assert isinstance(guild, discord.Guild)
            if guild.unavailable:
                server_temp_data["Unavailable"].add(guild.id)
                continue

            server_counter["Members"] += getattr(guild, "member_count", 0)
            if not lightmode:
                if detailed:
                    for feature in guild.features:
                        features_count[f"{features.get(f'{feature}') or 'Unknown'}"] += 1
                    verify_count[f"{verify.get(f'{guild.verification_level}') or 'Unknown'}"] += 1
                    #region_count[f"{vc_regions.get(f'{guild.region}') or 'Unknown'}"] += 1
                server_counter["Roles"] += len(guild.roles)
                if guild.large:
                    server_temp_data["Large"].add(guild.id)
                if not guild.chunked:
                    server_temp_data["Unchunked"].add(guild.id)
                guild_premium_tier = guild.premium_tier
                if guild_premium_tier != 0:
                    server_temp_data["Nitro Boosted"].add(guild.id)
                if guild_premium_tier == 1:
                    server_temp_data["Tier 1 Nitro"].add(guild.id)
                elif guild_premium_tier == 2:
                    server_temp_data["Tier 2 Nitro"].add(guild.id)
                elif guild_premium_tier == 3:
                    server_temp_data["Tier 3 Nitro"].add(guild.id)

                for channel in guild.channels:
                    server_counter["Server Channels"] += 1

                    if isinstance(channel, discord.TextChannel):
                        server_counter["Text Channels"] += 1
                        if channel.is_nsfw():
                            temp_data["NSFW Text Channels"].add(channel.id)
                        if channel.is_news():
                            temp_data["News Text Channels"].add(channel.id)
                        #if channel.type is discord.ChannelType.store:
                            #temp_data["Store Text Channels"].add(channel.id)
                    elif isinstance(channel, discord.VoiceChannel):
                        server_counter["Voice Channels"] += 1
                        counter["Users in a VC"] += len(channel.members)
                        if guild.me in channel.members:
                            counter["Users in a VC with me"] += len(channel.members) - 1
                            server_counter["Bots in a VC with me"] += (
                                sum(1 for m in channel.members if m.bot) - 1
                            )
                            for m in channel.members:
                                if m.is_on_mobile():
                                    temp_data["Users in a VC on Mobile"].add(m.id)
                    elif isinstance(channel, discord.CategoryChannel):
                        server_counter["Channel Categories"] += 1

                for emoji in guild.emojis:
                    assert isinstance(emoji, discord.Emoji)
                    server_counter["Emojis"] += 1
                    if emoji.animated:
                        server_counter["Animated Emojis"] += 1
                    else:
                        server_counter["Static Emojis"] += 1

            async for member in AsyncIter(guild.members, steps=1000):
                assert isinstance(member, discord.Member)
                temp_data["Unique Users"].add(member.id)
                if member.bot:
                    temp_data["Bots"].add(member.id)
                else:
                    temp_data["Humans"].add(member.id)

                # Not sure about let that next part in the future.
                if detailed:
                    if member.is_on_mobile():
                        temp_data["Mobile Users"].add(member.id)
                    streaming = False
                    async for a in AsyncIter(member.activities, steps=5, delay=0.01):
                        assert isinstance(a, (discord.BaseActivity, discord.Spotify))

                        if a.type is discord.ActivityType.streaming:
                            temp_data["Users Streaming"].add(member.id)
                            if member.bot:
                                temp_data["Bots Streaming"].add(member.id)
                            else:
                                temp_data["Humans Streaming"].add(member.id)
                            streaming = True
                        elif a.type is discord.ActivityType.playing:
                            temp_data["Users Gaming"].add(member.id)
                            if member.bot:
                                temp_data["Bots Gaming"].add(member.id)
                            else:
                                temp_data["Humans Gaming"].add(member.id)

                        if a.type is discord.ActivityType.listening:
                            temp_data["Users Listening"].add(member.id)
                            if member.bot:
                                temp_data["Bots Listening"].add(member.id)
                            else:
                                temp_data["Humans Listening"].add(member.id)
                        if a.type is discord.ActivityType.watching:
                            temp_data["Users Watching"].add(member.id)
                            if member.bot:
                                temp_data["Bots Watching"].add(member.id)
                            else:
                                temp_data["Humans Watching"].add(member.id)
                        if a.type is discord.ActivityType.custom:
                            temp_data["Users with Custom Status"].add(member.id)
                            if member.bot:
                                temp_data["Bots with Custom Status"].add(member.id)
                            else:
                                temp_data["Humans with Custom Status"].add(member.id)
                    if not streaming:
                        if member.status is not discord.Status.offline:
                            temp_data["Users Connected"].add(member.id)
                            if member.bot:
                                temp_data["Bots Connected"].add(member.id)
                            else:
                                temp_data["Humans Connected"].add(member.id)
                        if member.status is discord.Status.online:
                            temp_data["Users Online"].add(member.id)
                            if member.bot:
                                temp_data["Bots Online"].add(member.id)
                            else:
                                temp_data["Humans Online"].add(member.id)
                        elif member.status is discord.Status.idle:
                            temp_data["Idle Users"].add(member.id)
                            if member.bot:
                                temp_data["Idle Bots"].add(member.id)
                            else:
                                temp_data["Idle Humans"].add(member.id)
                        elif member.status is discord.Status.do_not_disturb:
                            temp_data["Users in Do Not Disturb"].add(member.id)
                            if member.bot:
                                temp_data["Bots in Do Not Disturb"].add(member.id)
                            else:
                                temp_data["Humans in Do Not Disturb"].add(member.id)
                        elif member.status is discord.Status.offline:
                            temp_data["Users Offline"].add(member.id)
                            if member.bot:
                                temp_data["Bots Offline"].add(member.id)
                            else:
                                temp_data["Human Offline"].add(member.id)

                    if member.mobile_status is discord.Status.online:
                        temp_data["Users Online on Mobile"].add(member.id)
                    elif member.mobile_status is discord.Status.idle:
                        temp_data["Users Idle on Mobile"].add(member.id)
                    elif member.mobile_status is discord.Status.do_not_disturb:
                        temp_data["Users in Do Not Disturb on Mobile"].add(member.id)
                    elif member.mobile_status is discord.Status.offline:
                        temp_data["Users Offline on Mobile"].add(member.id)

                    if member.desktop_status is discord.Status.online:
                        temp_data["Users Online on Desktop"].add(member.id)
                    elif member.desktop_status is discord.Status.idle:
                        temp_data["Users Idle on Desktop"].add(member.id)
                    elif member.desktop_status is discord.Status.do_not_disturb:
                        temp_data["Users in Do Not Disturb on Desktop"].add(member.id)
                    elif member.desktop_status is discord.Status.offline:
                        temp_data["Users Offline on Desktop"].add(member.id)

                    if member.web_status is discord.Status.online:
                        temp_data["Users Online on Browser"].add(member.id)
                    elif member.web_status is discord.Status.idle:
                        temp_data["Users Idle on Browser"].add(member.id)
                    elif member.web_status is discord.Status.do_not_disturb:
                        temp_data["Users in Do Not Disturb on Browser"].add(member.id)
                    elif member.web_status is discord.Status.offline:
                        temp_data["Users Offline on Browser"].add(member.id)

        for key, value in temp_data.items():
            counter[key] = len(value)
        for key, value in server_temp_data.items():
            server_counter[key] = len(value)

        for key, value in counter.items():
            setattr(bot.stats.bot, str(key), value)
        for key, value in server_counter.items():
            setattr(bot.stats.guilds, str(key), value)
        for key, value in region_count.items():
            setattr(bot.stats.guilds_regions, str(key), value)

        for key, value in features_count.items():
            setattr(bot.stats.guild_features, str(key), value)
        for key, value in verify_count.items():
            setattr(bot.stats.guild_verification, str(key), value)
    except Exception:
        log.exception("Exception in write_bot_data")


async def write_adventure_data(bot: Red, config_cache: SettingCacheManager):
    if (adv_cog := bot.get_cog("Adventure")) is None:
        return
    if await config_cache.get_set_lightmode():
        return
    try:
        raw_accounts = await adv_cog.config.all_users()
        raw_accounts_new = {}
        async for (k, v) in AsyncIter(raw_accounts.items(), steps=50):
            user_data = {}
            for item in ["adventures", "rebirths", "set_items"]:
                if item not in v:
                    if item == "adventures":
                        v.update(
                            {
                                item: {
                                    "wins": 0,
                                    "loses": 0,
                                    "fight": 0,
                                    "spell": 0,
                                    "talk": 0,
                                    "pray": 0,
                                    "run": 0,
                                    "fumbles": 0,
                                }
                            }
                        )
                    else:
                        v.update({item: 0})
            for (vk, vi) in v.items():
                if vk in ["rebirths", "set_items"]:
                    user_data.update({vk: vi})
                elif vk in ["adventures"]:
                    for (s, sv) in vi.items():
                        if s in {
                            "wins": 0,
                            "loses": 0,
                            "fight": 0,
                            "spell": 0,
                            "talk": 0,
                            "pray": 0,
                            "run": 0,
                            "fumbles": 0,
                        }:
                            user_data.update(vi)

            if user_data:
                user_data = {k: user_data}
            raw_accounts_new.update(user_data)
        adventure_count = Counter()
        async for u_id, u_data in AsyncIter(raw_accounts_new.items(), steps=25):
            adventure_count["Set Items"] += u_data.get("set_items", 0)
            adventure_count["Rebirths"] += u_data.get("rebirths", 0)
            adventure_count["Wins"] += u_data.get("wins", 0)
            adventure_count["Losses"] += u_data.get("loses", 0)
            adventure_count["Physical Attacks"] += u_data.get("fight", 0)
            adventure_count["Magical Attacks"] += u_data.get("spell", 0)
            adventure_count["Diplomatic Attacks"] += u_data.get("talk", 0)
            adventure_count["Prayers"] += u_data.get("pray", 0)
            adventure_count["Retreats"] += u_data.get("run", 0)
            adventure_count["Fumbles"] += u_data.get("fumbles", 0)

        setattr(bot.stats.adventure, "Wins", 0)
        setattr(bot.stats.adventure, "Losses", 0)

        for key, value in adventure_count.items():
            setattr(bot.stats.adventure, str(key), value)

        total_adventure = bot.stats.adventure.Wins + bot.stats.adventure.Losses
        if total_adventure:
            win_per = bot.stats.adventure.Wins / total_adventure
            loss_per = bot.stats.adventure.Losses / total_adventure
        else:
            win_per = 0
            loss_per = 0
        setattr(bot.stats.adventure, "Adventures", total_adventure)
        setattr(bot.stats.adventure, "Win Percentage", win_per * 100)
        setattr(bot.stats.adventure, "Loss Percentage", loss_per * 100)
    except Exception:
        log.exception("Exception in write_adventure_data")


async def write_audio_data(bot: Red, config_cache: SettingCacheManager):
    if await config_cache.get_set_lightmode():
        return
    try:
        counter = Counter()
        counter["Active Music Players"] = len(lavalink.active_players())
        counter["Music Players"] = len(lavalink.all_players())
        counter["Inactive Music Players"] = (
            counter["Music Players"] - counter["Active Music Players"]
        )
        detailed = await config_cache.get_set_detailed()
        # If you have MartTools cog loaded it will give you extra audio data
        mart_tools_cog = bot.get_cog("MartTools")
        if detailed and hasattr(mart_tools_cog, "get_value"):
            counter["Tracks Played"] = mart_tools_cog.get_value(
                "tracks_played", perma=True, raw=True
            )
            counter["Streams Played"] = mart_tools_cog.get_value(
                "streams_played", perma=True, raw=True
            )
            counter["YouTube Streams Played"] = mart_tools_cog.get_value(
                "yt_streams_played", perma=True, raw=True
            )
            counter["Mixer Streams Played"] = mart_tools_cog.get_value(
                "mixer_streams_played", perma=True, raw=True
            )
            counter["Twitch Streams Played"] = mart_tools_cog.get_value(
                "ttv_streams_played", perma=True, raw=True
            )
            counter["Other Streams Played"] = mart_tools_cog.get_value(
                "other_streams_played", perma=True, raw=True
            )
            counter["YouTube Videos Played"] = mart_tools_cog.get_value(
                "youtube_tracks", perma=True, raw=True
            )
            counter["SoundCloud Tracks Played"] = mart_tools_cog.get_value(
                "soundcloud_tracks", perma=True, raw=True
            )
            counter["Bandcamp Tracks Played"] = mart_tools_cog.get_value(
                "bandcamp_tracks", perma=True, raw=True
            )
            counter["Vimeo Tracks Played"] = mart_tools_cog.get_value(
                "vimeo_tracks", perma=True, raw=True
            )
            counter["Mixer Tracks Played"] = mart_tools_cog.get_value(
                "mixer_tracks", perma=True, raw=True
            )
            counter["TwichTV Videos Played"] = mart_tools_cog.get_value(
                "twitch_tracks", perma=True, raw=True
            )
            counter["Other Tracks Played"] = mart_tools_cog.get_value(
                "other_tracks", perma=True, raw=True
            )

        for key, value in counter.items():
            if isinstance(value, str):
                value = int(re.sub(r"\D", "", value))
            setattr(bot.stats.audio, str(key), value)
    except Exception:
        log.exception("Exception in write_audio_data")


async def write_shards_data(bot: Red):
    for index, latency in bot.latencies:
        setattr(bot.stats.shards, f"{index + 1}", int(latency * 1000))


async def write_currency_data(bot: Red):
    counter = Counter()
    accounts = await bank._config.all_users()
    overall = 0
    async for key, value in AsyncIter(list(accounts.items()), steps=50):
        overall += value["balance"]
    counter["Currency In Circulation"] = overall
    for key, value in counter.items():
        setattr(bot.stats.currency, str(key), value)


async def get_votes(bot: Red) -> Mapping:
    key = (await bot.get_shared_api_tokens("dbl")).get("api_key", "")
    if not key:
        return {}
    headers = {"Authorization": key}
    data = {}
    with contextlib.suppress(aiohttp.ServerTimeoutError, asyncio.TimeoutError):
        async with aiohttp.ClientSession(timeout=ClientTimeout(total=2)) as session:
            async with session.get(
                f"https://top.gg/api/bots/{bot.user.id}", headers=headers
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json(content_type=None)
    return data


def start_stats_tasks(bot: Red, config_cache: SettingCacheManager):
    bot._stats_task = bot.loop.create_task(update_task(bot, config_cache))


async def run_events(bot: Red, config_cache: SettingCacheManager):
    await asyncio.gather(
        *[
            write_bot_data(bot, config_cache),
            write_currency_data(bot),
            write_shards_data(bot),
            write_audio_data(bot, config_cache),
            write_adventure_data(bot, config_cache),
        ],
        return_exceptions=True,
    )


async def update_task(bot: Red, config_cache: SettingCacheManager):
    await bot.wait_until_red_ready()
    while True:
        try:
            await run_events(bot, config_cache)
        except Exception as exc:
            log.exception("update_task", exc_info=exc)
            await asyncio.sleep(10)
        else:
            if not bot._stats_ready.is_set():
                bot._stats_ready.set()
            await asyncio.sleep(60)


def _get_dict(self):
    return {k: v.__dict__ for k, v in self.stats.__dict__.items() if k != "to_dict"}


def init_bot_stats(bot: Red):
    bot_stats = (
        "guilds",
        "bot",
        "shards",
        "audio",
        "currency",
        "guilds_regions",
        "guild_features",
        "guild_verification",
        "adventure",
    )
    if not hasattr(bot, "stats"):
        bot.stats = SimpleNamespace()
    for stat_obj in bot_stats:
        if not hasattr(bot.stats, stat_obj):
            setattr(bot.stats, stat_obj, SimpleNamespace())
    if not hasattr(bot.stats, "to_dict"):
        bot.stats.to_dict = functools.partial(_get_dict, bot)
    if not hasattr(bot, "_stats_task"):
        bot._stats_task = None
    if not hasattr(bot, "_stats_ready"):
        bot._stats_ready = asyncio.Event()
