FROM python:3.5

ADD . /opt/mailman/

WORKDIR /opt/mailman/

RUN python setup.py develop

RUN mailman start

CMD ["tail", "-f", "/dev/null"]
