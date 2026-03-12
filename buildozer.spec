[app]

# Nome e identificatore
title = Card Printer Pro
package.name = cardprinterprо
package.domain = org.vanguard

# File sorgente
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json

# Versione
version = 1.0

# Dipendenze Python
requirements = python3,kivy==2.3.0,fpdf2,pillow

# Orientamento (portrait per uso comodo su telefono)
orientation = portrait

# Icona (opzionale, metti un file icon.png nella stessa cartella)
# icon.filename = %(source.dir)s/icon.png

# Android API
android.api = 33
android.minapi = 26
android.ndk = 25b
android.sdk = 33

# Permessi Android
android.permissions = READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE, INTERNET

# Architetture supportate
android.archs = arm64-v8a, armeabi-v7a

# Fullscreen
fullscreen = 0

[buildozer]
log_level = 2
warn_on_root = 1
