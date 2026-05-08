function registerVoicePreviewIpc(ipcMain, voicePreviewService) {
  ipcMain.handle("settings:checkVoicePreviews", async () => {
    return voicePreviewService.checkMissingPreviews();
  });

  ipcMain.handle("settings:getVoicePreviewUrl", async (_event, voice) => {
    return voicePreviewService.getPreviewUrl(voice);
  });

  ipcMain.handle("settings:generateVoicePreview", async (_event, voice) => {
    return voicePreviewService.generatePreview(voice);
  });
}

module.exports = {
  registerVoicePreviewIpc,
};
