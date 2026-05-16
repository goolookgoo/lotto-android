[app]
title = LottoPicker
package.name = lottopicker
package.domain = org.burlr.lotto

source.dir = .
source.include_exts = py,json,png,jpg,kv,atlas

version = 1.0.0

requirements = python3,kivy==2.3.0,certifi

orientation = portrait
fullscreen = 0

# 인터넷 권한 (dhlottery API 호출용)
android.permissions = INTERNET

# Android SDK / NDK
android.api = 33
android.minapi = 21
android.ndk_api = 21
android.archs = arm64-v8a, armeabi-v7a

# lotto_cache.json은 source.include_exts에 json이 포함되어 있어 자동 번들됨
android.allow_backup = True

# Kivy 로그 레벨
log_level = 2
warn_on_root = 1

[buildozer]
log_level = 2
