from huggingface_hub import snapshot_download

snapshot_download(
        repo_id="IndexTeam/IndexTTS-2.5",
        local_dir="checkpoints",
        local_dir_use_symlinks=False
        )
print("模型下载完成！")
