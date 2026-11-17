# Run the voice server in a terminal (make run) or in the background as a systemd user service.
SERVICE := terminalvox
UNIT    := $(HOME)/.config/systemd/user/$(SERVICE).service
LOG_DIR ?= $(CURDIR)/logs
SYSTEMCTL := systemctl --user

SILERO      := terminalvox-silero
SILERO_UNIT := $(HOME)/.config/systemd/user/$(SILERO).service

.PHONY: help run install uninstall start stop restart status logs journal \
        silero-run silero-install silero-uninstall silero-restart silero-status silero-journal

help:
	@echo "make run        - run in the terminal (Ctrl+C to stop)"
	@echo "make install    - install the service and start it at login"
	@echo "make start | stop | restart | status"
	@echo "make logs       - tail $(LOG_DIR)/bot.log"
	@echo "make journal    - systemd journal (run.sh output, startup errors)"
	@echo "make uninstall  - stop and remove the service"
	@echo "make silero-run | silero-install | silero-restart | silero-status | silero-journal | silero-uninstall"
	@echo "                - the local Silero synthesis service (TTS_PROVIDER=silero), see silero/server.py"

run:
	./run.sh

# The unit is regenerated from the template when the template or LOG_DIR changes.
$(UNIT): $(SERVICE).service.in Makefile
	mkdir -p $(@D) $(LOG_DIR)
	sed -e 's#@DIR@#$(CURDIR)#g' -e 's#@LOG_DIR@#$(LOG_DIR)#g' $< > $@
	$(SYSTEMCTL) daemon-reload

install: $(UNIT)
	$(SYSTEMCTL) enable $(SERVICE)

start: $(UNIT)
	$(SYSTEMCTL) start $(SERVICE)

stop:
	$(SYSTEMCTL) stop $(SERVICE)

restart: $(UNIT)
	$(SYSTEMCTL) restart $(SERVICE)

status:
	-$(SYSTEMCTL) --no-pager status $(SERVICE)

logs:
	tail -n 100 -F $(LOG_DIR)/bot.log

journal:
	journalctl --user -u $(SERVICE) -n 100 -f

uninstall:
	-$(SYSTEMCTL) disable --now $(SERVICE)
	rm -f $(UNIT)
	$(SYSTEMCTL) daemon-reload

# Local synthesis (TTS_PROVIDER=silero): a service of its own, so the model stays loaded across bot restarts.
$(SILERO_UNIT): silero/$(SILERO).service.in Makefile
	mkdir -p $(@D)
	sed -e 's#@DIR@#$(CURDIR)#g' $< > $@
	$(SYSTEMCTL) daemon-reload

silero-run:
	silero/run.sh

silero-install: $(SILERO_UNIT)
	$(SYSTEMCTL) enable --now $(SILERO)

silero-restart: $(SILERO_UNIT)
	$(SYSTEMCTL) restart $(SILERO)

silero-status:
	-$(SYSTEMCTL) --no-pager status $(SILERO)

silero-journal:
	journalctl --user -u $(SILERO) -n 100 -f

silero-uninstall:
	-$(SYSTEMCTL) disable --now $(SILERO)
	rm -f $(SILERO_UNIT)
	$(SYSTEMCTL) daemon-reload
