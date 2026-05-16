[app]
title = LottoPicker
package.name = lottopicker
package.domain = org.burlr.lotto

source.dir = .
source.include_exts = py,json,png,jpg,kv,atlas

version = 1.0.0

# Python 버전은 p4a 2024.01.21 릴리스의 python3 레시피 기본값(3.11.x)을 사용
requirements = python3,kivy==2.3.0,certifi

orientation = portrait
fullscreen = 0

android.permissions = INTERNET

# SDK / NDK
android.api = 33
android.minapi = 21
android.ndk_api = 21
android.archs = arm64-v8a, armeabi-v7a

# python-for-android v2024.01.21 (Python 3.11 기본, 안정 릴리스)
p4a.branch = v2024.01.21

android.allow_backup = True

log_level = 2
warn_on_root = 1

[buildozer]
log_level = 2
