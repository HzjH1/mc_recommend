# 二开推荐阅读[如何提高项目构建效率](https://developers.weixin.qq.com/miniprogram/dev/wxcloudrun/src/scene/build/speed.html)
# 选择构建用基础镜像（选择原则：在包含所有用到的依赖前提下尽可能体积小）。如需更换，请到[dockerhub官方仓库](https://hub.docker.com/_/python?tab=tags)自行选择后替换。
# 已知alpine镜像与pytorch有兼容性问题会导致构建失败，如需使用pytorch请务必按需更换基础镜像。
FROM node:22-alpine3.20

# 容器时区调整为上海时间，保证定时任务按本地时间触发
RUN apk add --no-cache tzdata \
&& cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
&& echo Asia/Shanghai > /etc/timezone

# 使用 HTTPS 协议访问容器云调用证书安装，并补齐 Python 运行环境
RUN apk add ca-certificates

# 选用国内镜像源以提高下载速度
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.tencent.com/g' /etc/apk/repositories \
&& apk add --update --no-cache python3 py3-pip \
&& rm -rf /var/cache/apk/*

# 拷贝当前项目到/app目录下(.dockerignore中文件除外)
COPY . /app

# 设定当前的工作目录
WORKDIR /app

# 安装依赖到指定的/install文件夹
# 选用国内镜像源以提高下载速度
RUN pip3 config set global.index-url http://mirrors.cloud.tencent.com/pypi/simple \
&& pip3 config set global.trusted-host mirrors.cloud.tencent.com \
&& pip3 install --break-system-packages --no-cache-dir --upgrade pip \
# pip install scipy 等数学包失败，可使用 apk add py3-scipy 进行， 参考安装 https://pkgs.alpinelinux.org/packages?name=py3-scipy&branch=v3.13
&& pip3 install --break-system-packages --no-cache-dir --user -r requirements.txt

# 安装最新版 npm 与最新版 @qwen-code/sdk
RUN npm install -g npm@latest @qwen-code/qwen-code@latest

# 周期任务：每周日 00:05 先生成周推荐，再触发该周午/晚自动订餐任务
RUN echo "5 0 * * 0 cd /app && /usr/bin/python3 manage.py run_weekly_recommendations --workdays 5 --top-n 3 && /usr/bin/python3 manage.py run_weekly_auto_order_jobs --workdays 5 >> /var/log/cron.log 2>&1" > /etc/crontabs/root

# 暴露端口
# 此处端口必须与「服务设置」-「流水线」以及「手动上传代码包」部署时填写的端口一致，否则会部署失败。
EXPOSE 80

# 执行启动命令
# 写多行独立的CMD命令是错误写法！只有最后一行CMD命令会被执行，之前的都会被忽略，导致业务报错。
# 请参考[Docker官方文档之CMD命令](https://docs.docker.com/engine/reference/builder/#cmd)
CMD ["sh", "-c", "touch /var/log/cron.log && crond -l 2 && python3 manage.py runserver 0.0.0.0:80"]
