"use client";

import { ArrowRight, Captions, Film, HardDrive, Video } from "lucide-react";
import { useLocalStorage } from "@/services/storage/use-local-storage";
import { useUiLanguage, LanguageSelect } from "@/i18n/ui-language";
import { Button } from "../ui/button";
import {
	Dialog,
	DialogBody,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogDescription,
} from "../ui/dialog";

export function Onboarding() {
	const t = useUiLanguage();
	const [seen, setSeen] = useLocalStorage({
		key: "hasSeenOnboarding",
		defaultValue: false,
	});
	return (
		<Dialog open={!seen} onOpenChange={() => setSeen({ value: true })}>
			<DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl sm:max-w-[480px]">
				<DialogHeader className="p-6 pr-14">
					<div className="flex items-center gap-3">
						<div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
							<Film className="size-5" aria-hidden="true" />
						</div>
						<DialogTitle className="text-base leading-snug">
							{t("Welcome to Lwent Editor")}
						</DialogTitle>
					</div>
				</DialogHeader>
				<DialogBody className="gap-6">
					<div className="flex flex-wrap items-center justify-between gap-3">
						<span className="text-sm text-muted-foreground">
							{t("Language")}
						</span>
						<LanguageSelect />
					</div>
					<div className="space-y-3">
						<h2 className="text-xl font-semibold leading-snug tracking-tight">
							{t("Edit, translate and review your video")}
						</h2>
						<DialogDescription className="leading-relaxed">
							{t(
								"Import a video, translate subtitles, then review each segment before exporting.",
							)}
						</DialogDescription>
					</div>
					<div className="grid grid-cols-3 gap-2">
						{[
							{ icon: Video, label: "Import video" },
							{ icon: Captions, label: "Edit subtitles" },
							{ icon: Film, label: "Export video" },
						].map(({ icon: Icon, label }, index) => (
							<div
								key={label}
								className="flex flex-col gap-3 rounded-xl border bg-muted/30 p-3"
							>
								<div className="flex items-center justify-between text-muted-foreground">
									<Icon className="size-4" aria-hidden="true" />
									<span className="text-xs">0{index + 1}</span>
								</div>
								<span className="text-xs font-medium leading-relaxed">
									{t(label)}
								</span>
							</div>
						))}
					</div>
					<div className="space-y-4">
						<Button
							className="h-11 w-full rounded-xl"
							onClick={() => setSeen({ value: true })}
						>
							{t("Get started")}
							<ArrowRight aria-hidden="true" />
						</Button>
						<p className="flex items-start justify-center gap-2 text-xs leading-relaxed text-muted-foreground">
							<HardDrive
								className="mt-0.5 size-3.5 shrink-0"
								aria-hidden="true"
							/>
							{t("Your changes are saved in this browser.")}
						</p>
					</div>
				</DialogBody>
			</DialogContent>
		</Dialog>
	);
}
