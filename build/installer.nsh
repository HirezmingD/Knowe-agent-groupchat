; Knowe NSIS lifecycle hooks.
; Keep user-owned data/ and Logs/ on uninstall and on in-place updates. The
; default electron-builder remover deletes the entire installation directory;
; overriding customRemoveFiles lets us delete only application payloads.

!macro customRemoveFiles
  ; The uninstaller starts with $INSTDIR as its working directory. Move out
  ; before removing payloads so Windows does not keep the directory busy.
  SetOutPath "$TEMP"

  RMDir /r "$INSTDIR\locales"
  RMDir /r "$INSTDIR\resources"
  RMDir /r "$INSTDIR\swiftshader"

  ; The installation root contains generated application payloads only. data/
  ; and Logs/ are directories, so deleting root files leaves them untouched.
  Delete /REBOOTOK "$INSTDIR\*.*"
  RMDir "$INSTDIR"
!macroend
