# Tasks

- [x] Add OpenSpec requirements for conversational agent loop parity.
- [x] Add a conversational strict loop entry point for CLI `chat`.
- [x] Route UI `/api/chat` to the conversational strict loop.
- [x] Add or reuse model-authored visible message actions (`brief` / `SendUserMessage`) so the UI can show real assistant progress without fake replies.
- [x] Ensure `skill` actions from chat can load nested skills and continue with their instructions.
- [x] Ensure `AskUserQuestion` is the only backend pause trigger for chat loop execution.
- [x] Remove or downgrade frontend heuristic question inference so it cannot create fake waiting state.
- [x] Keep process events out of the middle dialogue and keep them visible in the right process monitor.
- [x] Add generic selftests for chat skill invocation, question pause, non-pausing question text, and UI/process row classification.
- [x] Ensure answering a conversational pending question resumes with prior session replay/context instead of an isolated fresh chat.
- [x] Add a targeted selftest selector for conversational-loop regression checks.
- [x] Run hardcoding scan on changed files.
- [x] Run targeted runtime selftests.
- [x] Record verification evidence.
