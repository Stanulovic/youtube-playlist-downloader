    last_file = {"name": None, "title": None, "idx": None, "cnt": None}

    def hook(d):
        """
        Minimalistički log: Stavka i/n, Skidam -> Preuzeto -> Konvertujem (triggeruje se odmah),
        a 'Gotovo' stiže iz postprocessor hook-a.
        """
        try:
            info = d.get("info_dict", {}) or {}
            # pokušaj da izvučeš naslov i indeks iz info_dict
            title = info.get("title") or (os.path.basename(d.get("filename","")) if d.get("filename") else None)
            idx = info.get("playlist_index")
            cnt = info.get("n_entries") or info.get("playlist_count")

            if d["status"] == "downloading":
                # Loguj početak za svaku novu datoteku
                if d.get("filename"):
                    name = os.path.basename(d["filename"])
                    if name != last_file["name"]:
                        last_file.update({"name": name, "title": title, "idx": idx, "cnt": cnt})
                        if idx and cnt:
                            log(f"📦 Stavka {idx}/{cnt}")
                        if title:
                            log(f"▶️ Skidam: {title}")
                        else:
                            log(f"▶️ Skidam…")
            elif d["status"] == "finished" and d.get("filename"):
                # Završeno preuzimanje; najavi konverziju
                if last_file["title"]:
                    log("✅ Preuzeto")
                    log("🎧 Konvertujem")
                else:
                    log("✅ Preuzeto\n🎧 Konvertujem")
        except Exception:
            pass

    def pp_hook(d):
        """
        Kada FFmpegExtractAudio završi – označi 'Gotovo' i upiši naslov u rezime.
        """
        try:
            if d.get("status") == "finished" and d.get("postprocessor") == "FFmpegExtractAudio":
                info = (d.get("info_dict") or {})
                title = info.get("title")
                if title:
                    ready_titles.append(title)
                log("🟢 Gotovo")
        except Exception:
            pass
