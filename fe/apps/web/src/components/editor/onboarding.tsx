"use client";
import { useLocalStorage } from "@/services/storage/use-local-storage";
import { Button } from "../ui/button";
import {
	Dialog,
	DialogBody,
	DialogContent,
	DialogTitle,
	DialogDescription,
} from "../ui/dialog";
import { useUiLanguage, LanguageSelect } from "@/i18n/ui-language";
export function Onboarding() {
	const t = useUiLanguage();
	const [seen, setSeen] = useLocalStorage({
		key: "hasSeenOnboarding",
		defaultValue: false,
	});
	return (
		<Dialog open={!seen} onOpenChange={() => setSeen({ value: true })}>
			<DialogContent className="sm:max-w-[425px]">
				<DialogTitle>{t("Welcome to OpenCut")}</DialogTitle>
				<DialogBody>
					<div className="space-y-4">
						<LanguageSelect />
						<h2 className="font-medium">
							{t("Edit, translate and review your video")}
						</h2>
						<DialogDescription className="text-sm text-muted-foreground">
							{t(
								"Import a video, translate subtitles, then review each segment before exporting.",
							)}
						</DialogDescription>
						<p className="text-sm text-muted-foreground">
							{t("Your changes are saved in this browser.")}
						</p>
						<Button className="w-full" onClick={() => setSeen({ value: true })}>
							{t("Get started")}
						</Button>
					</div>
				</DialogBody>
			</DialogContent>
		</Dialog>
	);
}
