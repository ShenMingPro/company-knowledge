"""
测试模型工厂：验证生成稳定性配置正确应用
"""
import pytest
from unittest.mock import patch, MagicMock
from model.factory import create_chat_model, ChatModelFactory


class TestModelFactoryStability:
    """测试模型工厂的稳定性配置"""

    @patch('model.factory.ChatTongyi')
    @patch('model.factory.rag_conf', {
        'chat_model_name': 'qwen3-max',
        'chat_model_temperature': 0.0,
        'chat_model_top_p': 1.0,
        'judge_model_temperature': 0.0,
        'judge_model_top_p': 1.0
    })
    @patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key', 'MODEL_PROVIDER': 'dashscope'})
    def test_answer_model_has_temperature_zero(self, mock_tongyi):
        """测试：answer 模型实际参数包含 temperature=0"""
        mock_tongyi.return_value = MagicMock()

        model = create_chat_model(purpose="answer")

        # 验证 ChatTongyi 被调用，且 model_kwargs 包含正确的参数
        mock_tongyi.assert_called_once()
        call_kwargs = mock_tongyi.call_args[1]

        assert 'model_kwargs' in call_kwargs
        assert call_kwargs['model_kwargs']['temperature'] == 0.0
        assert call_kwargs['model_kwargs']['top_p'] == 1.0
        assert call_kwargs['streaming'] is True

    @patch('model.factory.ChatTongyi')
    @patch('model.factory.rag_conf', {
        'chat_model_name': 'qwen3-max',
        'chat_model_temperature': 0.0,
        'chat_model_top_p': 1.0,
        'judge_model_temperature': 0.0,
        'judge_model_top_p': 1.0
    })
    @patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key', 'MODEL_PROVIDER': 'dashscope'})
    def test_judge_model_has_temperature_zero(self, mock_tongyi):
        """测试：judge 模型实际参数包含 temperature=0"""
        mock_tongyi.return_value = MagicMock()

        model = create_chat_model(purpose="judge")

        # 验证 ChatTongyi 被调用，且 model_kwargs 包含正确的参数
        mock_tongyi.assert_called_once()
        call_kwargs = mock_tongyi.call_args[1]

        assert 'model_kwargs' in call_kwargs
        assert call_kwargs['model_kwargs']['temperature'] == 0.0
        assert call_kwargs['model_kwargs']['top_p'] == 1.0
        assert call_kwargs['streaming'] is False

    @patch('model.factory.ChatTongyi')
    @patch('model.factory.rag_conf', {
        'chat_model_name': 'qwen3-max',
        'chat_model_temperature': 0.0,
        'chat_model_top_p': 1.0
    })
    @patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key', 'MODEL_PROVIDER': 'dashscope'})
    def test_default_purpose_uses_chat_model_config(self, mock_tongyi):
        """测试：默认用途使用 chat_model 配置"""
        mock_tongyi.return_value = MagicMock()

        factory = ChatModelFactory(purpose=None)
        model = factory.generator()

        # 验证使用了 chat_model 的配置
        call_kwargs = mock_tongyi.call_args[1]
        assert call_kwargs['model_kwargs']['temperature'] == 0.0
        assert call_kwargs['model_kwargs']['top_p'] == 1.0

    @patch('model.factory.ChatTongyi')
    @patch('model.factory.rag_conf', {
        'chat_model_name': 'qwen3-max',
        'chat_model_temperature': 0.0,
        'chat_model_top_p': 1.0
    })
    @patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key', 'MODEL_PROVIDER': 'dashscope'})
    def test_api_key_not_in_model_metadata(self, mock_tongyi):
        """测试：API Key 不出现在模型元数据中"""
        mock_instance = MagicMock()
        mock_tongyi.return_value = mock_instance

        model = create_chat_model(purpose="answer")

        # 验证 API Key 通过参数传入，不会暴露在其他地方
        call_kwargs = mock_tongyi.call_args[1]
        assert 'dashscope_api_key' in call_kwargs

        # model_kwargs 中不应该包含 API Key
        assert 'dashscope_api_key' not in call_kwargs['model_kwargs']
        assert 'api_key' not in call_kwargs['model_kwargs']

    @patch.dict('os.environ', {'MODEL_PROVIDER': 'dashscope'}, clear=True)
    def test_missing_api_key_raises_error(self):
        """测试：缺少 API Key 抛出错误"""
        with pytest.raises(ValueError, match="未配置DASHSCOPE_API_KEY环境变量"):
            create_chat_model(purpose="answer")

    @patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key', 'MODEL_PROVIDER': 'ollama'})
    def test_unsupported_provider_raises_error(self):
        """测试：不支持的提供商抛出错误"""
        with pytest.raises(NotImplementedError, match="Ollama支持将在后续版本实现"):
            create_chat_model(purpose="answer")


class TestModelConfigIsolation:
    """测试 answer 和 judge 模型配置隔离"""

    @patch('model.factory.ChatTongyi')
    @patch('model.factory.rag_conf', {
        'chat_model_name': 'qwen3-max',
        'chat_model_temperature': 0.5,  # answer 使用 0.5
        'chat_model_top_p': 0.9,
        'judge_model_temperature': 0.0,  # judge 使用 0.0
        'judge_model_top_p': 1.0
    })
    @patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key', 'MODEL_PROVIDER': 'dashscope'})
    def test_answer_and_judge_use_different_configs(self, mock_tongyi):
        """测试：answer 和 judge 使用独立配置"""
        mock_tongyi.return_value = MagicMock()

        # 创建 answer 模型
        answer_model = create_chat_model(purpose="answer")
        answer_call_kwargs = mock_tongyi.call_args[1]

        assert answer_call_kwargs['model_kwargs']['temperature'] == 0.5
        assert answer_call_kwargs['model_kwargs']['top_p'] == 0.9

        # 重置 mock
        mock_tongyi.reset_mock()

        # 创建 judge 模型
        judge_model = create_chat_model(purpose="judge")
        judge_call_kwargs = mock_tongyi.call_args[1]

        assert judge_call_kwargs['model_kwargs']['temperature'] == 0.0
        assert judge_call_kwargs['model_kwargs']['top_p'] == 1.0
