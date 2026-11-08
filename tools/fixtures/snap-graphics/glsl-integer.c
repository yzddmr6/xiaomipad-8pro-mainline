// SPDX-License-Identifier: MIT
/* GLSL constant regressions for ir_constant (src/compiler/glsl/ir.cpp).
 * Run with the provider wrapper and softpipe, with the shader cache disabled.
 * The old assertion-enabled provider aborts in min/max optimization. Integer
 * arithmetic alone does not reproduce it. The mediump case is the shape of
 * Flutter Impeller's text shader: ir.cpp built by GCC 13 at -O3 on ARM64
 * loses float16 constants, so the folded comparison takes the else branch.
 * This is not a hardware GPU test.
 */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static GLuint shader(GLenum type, const char *source)
{
	GLuint object = glCreateShader(type);
	GLint ok;
	glShaderSource(object, 1, &source, NULL);
	glCompileShader(object);
	glGetShaderiv(object, GL_COMPILE_STATUS, &ok);
	if (!ok) {
		char log[2048];
		glGetShaderInfoLog(object, sizeof(log), NULL, log);
		fprintf(stderr, "shader compile failed: %s\n", log);
		exit(1);
	}
	return object;
}

static GLuint link(GLuint vert, const char *source)
{
	GLuint program = glCreateProgram();
	GLint ok;
	glAttachShader(program, vert);
	glAttachShader(program, shader(GL_FRAGMENT_SHADER, source));
	glBindAttribLocation(program, 0, "p");
	glLinkProgram(program);
	glGetProgramiv(program, GL_LINK_STATUS, &ok);
	if (!ok) {
		fprintf(stderr, "shader link failed\n");
		exit(1);
	}
	return program;
}

static void draw(GLuint program, const char *name, const unsigned char expected[4])
{
	unsigned char pixel[4];
	glUseProgram(program);
	glClear(GL_COLOR_BUFFER_BIT);
	glDrawArrays(GL_TRIANGLES, 0, 3);
	glReadPixels(8, 8, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
	if (glGetError() != GL_NO_ERROR) {
		fprintf(stderr, "GL draw/readback failed\n");
		exit(1);
	}
	for (unsigned i = 0; i < 4; i++) {
		if (abs((int)pixel[i] - expected[i]) > 1) {
			fprintf(stderr, "%s pixel component %u: expected %u, got %u\n",
				name, i, expected[i], pixel[i]);
			exit(1);
		}
	}
	printf("%s pixel=%u,%u,%u,%u\n", name, pixel[0], pixel[1], pixel[2], pixel[3]);
}

int main(void)
{
	EGLDisplay display;
	EGLConfig config;
	EGLSurface surface;
	EGLContext context;
	EGLint count;
	const unsigned char expected[4] = { 51, 153, 230, 255 };
	const unsigned char expected_mediump[4] = { 204, 204, 204, 204 };
	const GLfloat vertices[] = { -1, -1, 3, -1, -1, 3 };
	const EGLint attributes[] = {
		EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
		EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE
	};
	const EGLint size[] = { EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE };
	const EGLint version[] = { EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE };
	setbuf(stdout, NULL);
	display = eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
	if (display == EGL_NO_DISPLAY || !eglInitialize(display, NULL, NULL) ||
	    !eglBindAPI(EGL_OPENGL_ES_API) || !eglChooseConfig(display, attributes, &config, 1, &count) || !count) {
		fprintf(stderr, "EGL setup failed: 0x%x\n", eglGetError());
		return 1;
	}
	surface = eglCreatePbufferSurface(display, config, size);
	context = eglCreateContext(display, config, EGL_NO_CONTEXT, version);
	if (surface == EGL_NO_SURFACE || context == EGL_NO_CONTEXT || !eglMakeCurrent(display, surface, surface, context)) {
		fprintf(stderr, "EGL context failed: 0x%x\n", eglGetError());
		return 1;
	}
	const char *renderer = (const char *)glGetString(GL_RENDERER);
	printf("renderer=%s\nversion=%s\n", renderer ? renderer : "(null)", glGetString(GL_VERSION));
	if (!renderer || strcmp(renderer, "softpipe") != 0) {
		fprintf(stderr, "Required renderer missing\n");
		return 1;
	}
	/* Verify the actual driver mapping, not just GL_VERSION or ldd. */
	FILE *maps = fopen("/proc/self/maps", "r");
	char line[4096];
	int found = 0;
	if (!maps) { perror("maps"); return 1; }
	while (fgets(line, sizeof(line), maps)) {
		char *path = strchr(line, '/');
		if (path && strstr(path, "/libgallium-")) {
			printf("gallium=%s", path);
			found = 1;
			break;
		}
	}
	fclose(maps);
	if (!found) { fprintf(stderr, "Provider Gallium mapping missing\n"); return 1; }
	GLuint vert = shader(GL_VERTEX_SHADER,
		"#version 300 es\nin vec2 p; void main(){gl_Position=vec4(p,0.,1.);}");
	GLuint integer = link(vert,
		"#version 300 es\nprecision highp float; precision highp int;\n"
		"uniform int selector; uniform float value; out vec4 color;\n"
		"void main(){int k=(selector+1)*2-1;"
		"float s=min(max(value,0.0),1.0);"
		"color=vec4(.2,.6,.9,1.)*s*float(k);}");
	GLuint mediump = link(vert,
		"#version 300 es\nprecision mediump float;\n"
		"const float k=1.0; uniform float value; out vec4 color;\n"
		"void main(){float c=0.0; if(k==1.0) c=value; else c=0.25; color=vec4(c);}");
	puts("shader_compile_link=PASS");
	glViewport(0, 0, 16, 16);
	glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, vertices);
	glEnableVertexAttribArray(0);
	glUseProgram(integer);
	glUniform1i(glGetUniformLocation(integer, "selector"), 0);
	glUniform1f(glGetUniformLocation(integer, "value"), 1.0f);
	draw(integer, "integer", expected);
	glUseProgram(mediump);
	glUniform1f(glGetUniformLocation(mediump, "value"), 0.8f);
	draw(mediump, "mediump", expected_mediump);
	puts("render_readback=PASS");
	glDeleteProgram(integer);
	glDeleteProgram(mediump);
	glDeleteShader(vert);
	eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
	eglDestroyContext(display, context);
	eglDestroySurface(display, surface);
	eglTerminate(display);
	return 0;
}
