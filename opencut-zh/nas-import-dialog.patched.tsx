"use client";

import { useEffect, useState } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

type NasItem = {
	id: number;
	name: string;
	kind: string;
	size: number;
	taken_at: string;
	thumb_url: string;
};

const KIND_LABEL: Record<string, string> = {
	video: "视频",
	photo: "照片",
	pano: "全景",
	raw: "RAW",
	audio: "音频",
};

function formatSize(bytes: number): string {
	if (!bytes) return "";
	const units = ["B", "KB", "MB", "GB"];
	let v = bytes;
	let i = 0;
	while (v >= 1024 && i < units.length - 1) {
		v /= 1024;
		i += 1;
	}
	return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function NasImportDialog({
	open,
	onOpenChange,
	onImportFiles,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onImportFiles: (files: File[]) => void;
}) {
	const [workbenchUrl, setWorkbenchUrl] = useState("");
	const [items, setItems] = useState<NasItem[]>([]);
	const [selected, setSelected] = useState<Set<number>>(new Set());
	const [loading, setLoading] = useState(false);
	const [importing, setImporting] = useState(false);

	useEffect(() => {
		if (!open) return;
		const saved = localStorage.getItem("djiwbUrl");
		const secure = window.location.protocol === "https:";
		const fallback = secure
			? `https://${window.location.hostname}:8921`
			: `${window.location.protocol}//${window.location.hostname}:8920`;
		let url = saved || fallback;
		// 旧存档升级：https 页面里 http 地址会被混合内容拦截，自动升到 TLS 网关
		if (secure && url.startsWith("http:")) {
			url = url.replace("http://", "https://").replace(":8920", ":8921");
		}
		setWorkbenchUrl(url);
		setItems([]);
		setSelected(new Set());
		void loadItems(url);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open]);

	async function loadItems(url: string) {
		setLoading(true);
		try {
			const resp = await fetch(`${url.replace(/\/$/, "")}/api/items?per_page=200&sort=date_desc`, {
				credentials: "include",
			});
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			const json = (await resp.json()) as { items: NasItem[] };
			const allowed = new Set(["photo", "video", "pano", "raw", "audio"]);
			setItems(
				json.items
					.map((i) => ({ ...i, name: ((i as { rel_path?: string }).rel_path || (i as { file_name?: string }).file_name || "").split("/").pop() || i.name }))
					.filter((i) => allowed.has(i.kind)),
			);
		} catch (error) {
			toast.error(
				`无法连接素材库: ${error instanceof Error ? error.message : String(error)}`,
			);
		} finally {
			setLoading(false);
		}
	}

	function toggle(id: number) {
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	}

	async function importSelected() {
		const picked = items.filter((i) => selected.has(i.id));
		if (picked.length === 0) {
			toast.error("请先勾选素材");
			return;
		}
		setImporting(true);
		const files: File[] = [];
		try {
			for (const item of picked) {
				const resp = await fetch(`${workbenchUrl.replace(/\/$/, "")}/api/items/${item.id}/file`, {
					credentials: "include",
				});
				if (!resp.ok) throw new Error(`${item.name}: HTTP ${resp.status}`);
				const blob = await resp.blob();
				files.push(new File([blob], item.name, { type: blob.type }));
			}
			onOpenChange(false);
			onImportFiles(files);
		} catch (error) {
			toast.error(
				`导入失败: ${error instanceof Error ? error.message : String(error)}`,
			);
		} finally {
			setImporting(false);
		}
	}

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-xl">
				<DialogHeader>
					<DialogTitle>从 DJI 素材库导入</DialogTitle>
					<DialogDescription>
						选择 NAS 素材工作台里的文件，直接导入当前剪辑项目。
					</DialogDescription>
				</DialogHeader>
				<div className="flex items-center gap-2">
					<Input
						value={workbenchUrl}
						onChange={(e) => setWorkbenchUrl(e.target.value)}
						placeholder="http://192.168.5.10:8920"
					/>
					<Button
						variant="outline"
						onClick={() => {
							localStorage.setItem("djiwbUrl", workbenchUrl);
							void loadItems(workbenchUrl);
						}}
					>
						刷新
					</Button>
				</div>
				<div className="max-h-80 overflow-y-auto rounded-md border">
					{loading ? (
						<div className="p-6 text-center text-sm text-muted-foreground">
							加载中…
						</div>
					) : items.length === 0 ? (
						<div className="p-6 text-center text-sm text-muted-foreground">
							没有可用素材
						</div>
					) : (
						<div className="divide-y">
							{items.map((item) => (
								<label
									key={item.id}
									className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-accent/40"
								>
									<Checkbox
										checked={selected.has(item.id)}
										onCheckedChange={() => toggle(item.id)}
									/>
									{item.thumb_url ? (
										// eslint-disable-next-line @next/next/no-img-element
										<img
											src={`${workbenchUrl.replace(/\/$/, "")}${item.thumb_url}`}
											alt=""
											className="h-10 w-14 rounded object-cover"
										/>
									) : (
										<div className="h-10 w-14 rounded bg-muted" />
									)}
									<span className="flex-1 truncate text-sm">{item.name}</span>
									<span className="text-xs text-muted-foreground">
										{KIND_LABEL[item.kind] ?? item.kind}
										{item.size ? ` · ${formatSize(item.size)}` : ""}
									</span>
								</label>
							))}
						</div>
					)}
				</div>
				<DialogFooter>
					<Button
						variant="outline"
						onClick={() => setSelected(new Set(items.map((i) => i.id)))}
					>
						全选
					</Button>
					<Button
						onClick={() => void importSelected()}
						disabled={importing || selected.size === 0}
					>
						{importing ? "导入中…" : `导入 ${selected.size} 项`}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
