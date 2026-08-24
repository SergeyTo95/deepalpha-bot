from pathlib import Path


path = Path("run_web_process.py")
text = path.read_text()

replacements = [
    (
        "    from services.velia_chat_latency_runtime_patch import install as install_velia_chat_latency\n",
        "    from services.velia_chat_latency_runtime_patch import install as install_velia_chat_latency\n"
        "    from services.velia_code_archive_runtime_patch import install as install_velia_code_archive\n"
        "    from services.velia_code_archive_service import ensure_velia_code_archive_tables\n",
    ),
    (
        "    from velia_image_routes import setup_velia_image_routes\n",
        "    from velia_code_archive_routes import setup_velia_code_archive_routes\n"
        "    from velia_image_routes import setup_velia_image_routes\n",
    ),
    (
        "    setup_velia_image_routes(deepalpha_web.app)\n    setup_velia_video_routes(deepalpha_web.app)\n",
        "    setup_velia_image_routes(deepalpha_web.app)\n"
        "    setup_velia_code_archive_routes(deepalpha_web.app)\n"
        "    setup_velia_video_routes(deepalpha_web.app)\n",
    ),
    (
        "    install_velia_software_factory_chat(velia_chat_service_module)\n"
        "    install_velia_software_factory_autonomy(deepalpha_web.app)\n",
        "    install_velia_software_factory_chat(velia_chat_service_module)\n"
        "    # Code archive routing is outermost: an explicit ZIP request after a\n"
        "    # completed Coding Agent run is served deterministically without\n"
        "    # entering Developer/Agent/Factory planning again.\n"
        "    install_velia_code_archive(velia_chat_service_module)\n"
        "    install_velia_software_factory_autonomy(deepalpha_web.app)\n",
    ),
    (
        "        ensure_velia_image_tables()\n        ensure_velia_video_tables()\n",
        "        ensure_velia_image_tables()\n"
        "        ensure_velia_code_archive_tables()\n"
        "        ensure_velia_video_tables()\n",
    ),
]

for old, new in replacements:
    if new in text:
        continue
    if text.count(old) != 1:
        raise SystemExit(f"bootstrap anchor mismatch: {old!r} count={text.count(old)}")
    text = text.replace(old, new, 1)

path.write_text(text)
